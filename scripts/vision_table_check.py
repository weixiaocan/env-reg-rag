"""Settle disputed table cells with a vision model as a fourth, independent reader.

For every unresolved table in ``data/registry/table-checks.json`` each
disputed cell is cropped from the rendered page (the cell box only) and sent
alone to the vision model, which sees no candidate reading.  A cell is
settled when the model's reading equals, ignoring whitespace and width forms:
- the recognized cell text -> confirmed as is;
- an independent reading (Paddle run, or the PDF text layer) -> corrected.
The corrected text is the model's own transcription (it keeps superscripts
the OCR runs flatten).  Cells the model settles nowhere stay pending for the
crop check.  A table is admitted when no pending cell or structural problem
is left.

Sending is gated by explicit owner authorization (provider, model, base URL,
send scope, budget) through ``external_run_guard``.  Every request and reply
is logged under ``data/quality/table_vision/<run id>/``.

    python scripts/vision_table_check.py --limit-tables 4 \
        --authorized-provider zhipu --authorized-model glm-4.5v \
        --authorized-base-url https://open.bigmodel.cn/api/paas/v4/ \
        --authorized-send-scope unresolved-table-cell-crops \
        --authorization-note "..." --budget-cny 30 --per-call-reserve-cny 0.2
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from glob import glob
from typing import Any

import pymupdf

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from scripts.check_tables import _pdf_path, grid_problems, norm  # noqa: E402
from scripts.external_run_guard import (  # noqa: E402
    add_verified_cost, can_start_call, validate_external_run,
)
from src.adapters.llm_provider import LlmProviderSettings  # noqa: E402
from src.application.table_checks import REGISTRY_PATH  # noqa: E402

SEND_SCOPE = "unresolved-table-cell-crops"
PROMPT = (
    "图片是一份标准规范里表格的一个单元格。请逐字转写单元格内印刷的文字，"
    "不要纠错、不要补全、不要解释、不要加标点。上标用 Unicode 上标字符（如 m³），"
    "下标写成普通字符（如 Di、CODCr）。单元格内换行用一个空格代替。"
    "单元格为空就输出空字符串。只输出 JSON：{\"text\": \"...\"}"
)


def _crop_png(page: pymupdf.Page, box: list[float]) -> bytes:
    rect = pymupdf.Rect(box) + (-1.0, -1.0, 1.0, 1.0)
    # Small cells are rendered larger so the model sees at least ~96 px.
    zoom = max(300 / 72, 96 / max(1.0, min(rect.width, rect.height)))
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect).tobytes("png")


def _parse(reply: str) -> str | None:
    text = reply.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value.get("text") if isinstance(value, dict) and isinstance(value.get("text"), str) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit-tables", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    for name in ("provider", "model", "base-url", "send-scope"):
        parser.add_argument(f"--authorized-{name}", required=True)
    parser.add_argument("--authorization-note", required=True)
    parser.add_argument("--budget-cny", type=float, required=True)
    parser.add_argument("--per-call-reserve-cny", type=float, required=True)
    parser.add_argument("--input-cost-per-million", type=float, required=True)
    parser.add_argument("--output-cost-per-million", type=float, required=True)
    args = parser.parse_args()

    from dotenv import dotenv_values
    env = {**dotenv_values(os.path.join(_REPO_ROOT, ".env")), **os.environ,
           "LLM_PROVIDER": args.authorized_provider, "LLM_MODEL": args.authorized_model,
           "LLM_INPUT_COST_PER_MILLION": str(args.input_cost_per_million),
           "LLM_OUTPUT_COST_PER_MILLION": str(args.output_cost_per_million),
           "LLM_COST_CURRENCY": "CNY"}
    env.pop("LLM_BASE_URL", None)
    settings = LlmProviderSettings.from_env({k: v for k, v in env.items() if v is not None})
    authorization = validate_external_run(
        settings, expected_scope=SEND_SCOPE, provider=args.authorized_provider,
        model=args.authorized_model, base_url=args.authorized_base_url,
        send_scope=args.authorized_send_scope, note=args.authorization_note,
        budget_cny=args.budget_cny, per_call_reserve_cny=args.per_call_reserve_cny)

    from openai import OpenAI
    client = OpenAI(api_key=settings.api_key.get_secret_value(), base_url=settings.base_url,
                    timeout=settings.timeout_seconds, max_retries=settings.max_retries)

    registry_path = os.path.join(_REPO_ROOT, REGISTRY_PATH)
    with open(registry_path, encoding="utf-8") as fh:
        registry = json.load(fh)
    targets = [e for e in registry["tables"]
               if e["verdict"] == "unresolved" and e.get("pending_cells")
               and e["method"] != "agent_crop_check"]
    if args.limit_tables:
        targets = targets[:args.limit_tables]
    run_id = "tv-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = os.path.join(_REPO_ROOT, "data", "quality", "table_vision", run_id)
    os.makedirs(log_dir)
    log = open(os.path.join(log_dir, "calls.jsonl"), "w", encoding="utf-8")
    lock = threading.Lock()
    spent = [0.0]
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    docs, pdfs = {}, {}

    def _cells(entry):
        if entry["sha256"] not in docs:
            path = glob(os.path.join(_REPO_ROOT, "data", "canonical", "v2",
                                     registry["generated_from"], entry["sha256"] + "*"))[0]
            with open(path, encoding="utf-8") as fh:
                docs[entry["sha256"]] = json.load(fh)
            pdfs[entry["sha256"]] = pymupdf.open(_pdf_path(docs[entry["sha256"]]))
        element = next(e for e in docs[entry["sha256"]]["elements"]
                       if e["element_id"] == entry["element_id"])
        return element["content"].get("cells") or []

    def cell_box(entry, row, column):
        cell = next(c for c in _cells(entry) if c["row"] == row and c["column"] == column)
        return pdfs[entry["sha256"]][entry["page"] - 1], cell["source_bbox"]

    jobs = []
    for entry in targets:
        for pending in entry["pending_cells"]:
            page, box = cell_box(entry, pending["row"], pending["column"])
            jobs.append((entry, pending, _crop_png(page, box)))

    def read(job):
        entry, pending, png = job
        with lock:
            if not can_start_call(spent[0], authorization):
                return job, None, "budget"
        reply, error = None, None
        try:
            response = client.chat.completions.create(
                model=settings.model, temperature=0.0,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                    {"type": "text", "text": PROMPT}]}],
                extra_body={"thinking": {"type": "disabled"}})
            reply = response.choices[0].message.content or ""
            usage = response.usage
            cost = (usage.prompt_tokens * settings.input_cost_per_million
                    + usage.completion_tokens * settings.output_cost_per_million) / 1e6
            with lock:
                spent[0] = add_verified_cost(
                    spent[0], {"estimated_cost": cost, "pricing_status": "calculated"}, authorization)
                usage_total["prompt_tokens"] += usage.prompt_tokens
                usage_total["completion_tokens"] += usage.completion_tokens
                usage_total["calls"] += 1
        except Exception as exc:  # logged and left pending
            error = f"{type(exc).__name__}: {exc}"
        with lock:
            log.write(json.dumps({
                "sha256": entry["sha256"], "region_id": entry["region_id"],
                "row": pending["row"], "column": pending["column"],
                "crop_sha256": hashlib.sha256(png).hexdigest(), "reply": reply, "error": error,
            }, ensure_ascii=False) + "\n")
            log.flush()
        return job, reply, error

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(read, jobs))

    readings: dict[tuple[str, str], dict[tuple[int, int], str | None]] = {}
    for (entry, pending, _), reply, error in results:
        readings.setdefault((entry["sha256"], entry["region_id"]), {})[
            (pending["row"], pending["column"])] = None if error or reply is None else _parse(reply)

    summary = {"tables": len(targets), "cells": len(jobs), "confirmed": 0, "corrected": 0,
               "unsettled": 0, "admitted": 0}
    for entry in targets:
        got = readings.get((entry["sha256"], entry["region_id"]), {})
        still = []
        for pending in entry["pending_cells"]:
            vision = got.get((pending["row"], pending["column"]))
            others = [pending[k] for k in ("pp_clean", "pp_raw", "text_layer") if k in pending]
            if vision is not None and norm(vision) == norm(pending["cell"]):
                summary["confirmed"] += 1
            elif vision is not None and norm(vision) and any(norm(vision) == norm(o) for o in others):
                entry["corrections"].append({"row": pending["row"], "column": pending["column"],
                                             "from": pending["cell"], "to": vision.strip()})
                summary["corrected"] += 1
            else:
                still.append({**pending, "vision": vision})
                summary["unsettled"] += 1
        entry["pending_cells"] = still
        entry["method"] = "vision_model_cell_check"
        entry["evidence"] = {**entry.get("evidence", {}), "vision_run": run_id,
                             "vision_model": settings.model}
        grid = [p for p in grid_problems(_cells(entry)) if p not in entry.get("problems", [])]
        entry["problems"] = [*entry.get("problems", []), *grid]
        if not still and not entry.get("problems"):
            entry["verdict"] = "admit"
            summary["admitted"] += 1
    registry.setdefault("rules", {})["vision_model_cell_check"] = (
        "Each disputed cell is cropped alone and read by the vision model without any candidate "
        "text. Equal to the recognized text (ignoring whitespace and width forms): confirmed. "
        "Equal to an independent reading (Paddle run or text layer): corrected to the model's "
        "transcription. Otherwise the cell stays pending.")
    with open(registry_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=1)
    record = {"run_id": run_id, "authorization": authorization.to_record(),
              "rates_cny_per_million": [settings.input_cost_per_million,
                                        settings.output_cost_per_million],
              "usage": usage_total, "estimated_cost_cny": round(spent[0], 4),
              "seconds": round(time.time() - started, 1), "summary": summary}
    with open(os.path.join(log_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
    print(json.dumps(record, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
