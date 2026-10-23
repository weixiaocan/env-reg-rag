"""Re-read whole tables whose recognized grid is wrong, with a vision model.

For every table in ``data/registry/table-checks.json`` that is unresolved
because of its structure (cell boxes that disagree with the grid, cells
without boxes, text outside every cell), the table area of the rendered page
is sent to the vision model, which returns an HTML table.  The proposal is
parsed into a grid and compared, as a bag of characters and a multiset of
numbers, with the independent reading of the same area (both Paddle runs,
or the PDF text layer).  Nothing is written to the registry: every proposal
is reviewed against the page before it may replace a table.

Sending is gated by explicit owner authorization through
``external_run_guard``; requests and replies are logged under
``data/quality/table_vision/<run id>/``.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from glob import glob
from html.parser import HTMLParser

import pymupdf

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from scripts.check_tables import _pdf_path, norm  # noqa: E402
from scripts.external_run_guard import (  # noqa: E402
    add_verified_cost, can_start_call, validate_external_run,
)
from src.adapters.llm_provider import LlmProviderSettings  # noqa: E402
from src.application.table_checks import REGISTRY_PATH  # noqa: E402

SEND_SCOPE = "unresolved-table-crops"
PROMPT = (
    "图片是一份标准规范里的一张表格。请按原样转写为一个 HTML <table>：每个印刷单元格对应一个 <td>，"
    "合并单元格用 rowspan/colspan 表示，行列结构必须与图片一致。单元格文字逐字转写，不要纠错、不要补全、"
    "不要解释。上标用 Unicode 上标字符（如 m³），下标写成普通字符（如 Di、CODCr）。单元格内换行用一个空格代替。"
    "空单元格写 <td></td>。表格上方的表题和下方的注不要写进表格。只输出 <table>…</table>。"
)
STRUCTURAL = ("grid_", "cells_without_boxes", "text_outside_cells", "pp_clean_text_outside",
              "pp_raw_text_outside", "no_text_layer")


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict]] = []
        self.cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th") and self.rows:
            self.cell = {"text": "", "row_span": int(a.get("rowspan") or 1),
                         "column_span": int(a.get("colspan") or 1)}
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.cell["text"] = re.sub(r"\s+", " ", self.cell["text"]).strip()
            self.rows[-1].append(self.cell)
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


def parse_grid(html: str) -> dict | None:
    """HTML table -> {row_count, column_count, cells}; None if spans do not tile."""
    parser = _Table()
    parser.feed(html)
    taken: set[tuple[int, int]] = set()
    cells = []
    for r, row in enumerate(parser.rows):
        c = 0
        for cell in row:
            while (r, c) in taken:
                c += 1
            for i in range(r, r + cell["row_span"]):
                for j in range(c, c + cell["column_span"]):
                    taken.add((i, j))
            cells.append({"row": r, "column": c, **cell})
            c += cell["column_span"]
    if not cells:
        return None
    rows = max(i for i, _ in taken) + 1
    columns = max(j for _, j in taken) + 1
    if len(taken) != rows * columns:
        return None
    return {"row_count": rows, "column_count": columns, "cells": cells}


def _numbers(text: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:\.\d+)?", norm(text)))


def compare(grid: dict, reference: str) -> dict:
    mine = Counter(norm("".join(c["text"] for c in grid["cells"])))
    ref = Counter(norm(reference))
    common = sum((mine & ref).values())
    return {"char_precision": round(common / max(1, sum(mine.values())), 3),
            "char_recall": round(common / max(1, sum(ref.values())), 3),
            "numbers_equal": _numbers("".join(c["text"] for c in grid["cells"])) == _numbers(reference)}


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
                    timeout=180, max_retries=settings.max_retries)

    with open(os.path.join(_REPO_ROOT, REGISTRY_PATH), encoding="utf-8") as fh:
        registry = json.load(fh)
    targets = [e for e in registry["tables"] if e["verdict"] == "unresolved"
               and any(p.startswith(STRUCTURAL) for p in e.get("problems") or [])]
    if args.limit_tables:
        targets = targets[:args.limit_tables]
    run_id = "tf-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = os.path.join(_REPO_ROOT, "data", "quality", "table_vision", run_id)
    os.makedirs(log_dir)
    lock = threading.Lock()
    spent = [0.0]
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    runs = registry.get("independent_ocr_runs") or {}
    run_dir = os.path.join(_REPO_ROOT, "data", "model_runtime", "independent_ocr", "runs")

    jobs = []
    docs: dict[str, dict] = {}
    for entry in targets:
        if entry["sha256"] not in docs:
            path = glob(os.path.join(_REPO_ROOT, "data", "canonical", "v2",
                                     registry["generated_from"], entry["sha256"] + "*"))[0]
            with open(path, encoding="utf-8") as fh:
                docs[entry["sha256"]] = json.load(fh)
        doc = docs[entry["sha256"]]
        element = next(e for e in doc["elements"] if e["element_id"] == entry["element_id"])
        box = element["source_spans"][0]["bbox"]
        page = pymupdf.open(_pdf_path(doc))[entry["page"] - 1]
        rect = pymupdf.Rect(box) + (-3, -3, 3, 3)
        png = page.get_pixmap(dpi=200, clip=rect).tobytes("png")
        references = {}
        for source, run in runs.items():
            p = os.path.join(run_dir, run, "pages", entry["sha256"], f"p{entry['page']:04d}.json")
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    lines = json.load(fh).get("lines") or []
                references[source] = "".join(
                    line["text"] for line in lines if line.get("bbox")
                    and rect.contains(pymupdf.Point((line["bbox"][0] + line["bbox"][2]) / 2,
                                                    (line["bbox"][1] + line["bbox"][3]) / 2)))
        if not references:
            references["text_layer"] = page.get_text("text", clip=rect)
        jobs.append((entry, png, references))

    def read(job):
        entry, png, references = job
        with lock:
            if not can_start_call(spent[0], authorization):
                return {"region_id": entry["region_id"], "error": "budget"}
        reply, error = None, None
        try:
            response = client.chat.completions.create(
                model=settings.model, temperature=0.0, max_tokens=8192,
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
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        grid = parse_grid(reply) if reply else None
        return {"sha256": entry["sha256"], "region_id": entry["region_id"],
                "element_id": entry["element_id"], "file_name": entry["file_name"],
                "page": entry["page"], "crop_sha256": hashlib.sha256(png).hexdigest(),
                "reply": reply, "error": error, "grid": grid,
                "checks": {k: compare(grid, v) for k, v in references.items()} if grid else None}

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(read, jobs))
    with open(os.path.join(log_dir, "tables.jsonl"), "w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    record = {"run_id": run_id, "authorization": authorization.to_record(),
              "rates_cny_per_million": [settings.input_cost_per_million,
                                        settings.output_cost_per_million],
              "usage": usage_total, "estimated_cost_cny": round(spent[0], 4),
              "seconds": round(time.time() - started, 1),
              "tables": len(results), "parsed": sum(1 for r in results if r["grid"]),
              "errors": sum(1 for r in results if r["error"])}
    with open(os.path.join(log_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
    print(json.dumps(record, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
