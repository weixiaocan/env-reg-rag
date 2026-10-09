#!/usr/bin/env python
"""Measure PP-OCRv6 alignment and prepare a fixed, image-backed calibration set."""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.ingestion.independent_ocr_fusion import (  # noqa: E402
    align_page,
    fusion_action,
    load_independent_ocr_page,
    load_independent_ocr_run,
)

CORPUS = "corpus-0b9a1fd84e3d"
RUN_ID = "ocrv6-ef06411e1606ba35"
SEED = 20261009


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _excluded_pages() -> set[tuple[str, int]]:
    rows = _read_jsonl(ROOT / "eval/audits/ocr_sampling/sample_sheet.jsonl")
    return {
        (row["document_sha256"], int(page))
        for row in rows for page in row.get("physical_pages") or []
    }


def _serialise_row(row: dict[str, Any], doc: dict[str, Any], page: int) -> dict[str, Any]:
    primary, pp = row.get("primary"), row.get("pp")
    anchor = pp or primary or {}
    return {
        "document_sha256": doc["sha256"],
        "document_name": doc.get("file_name"),
        "physical_page": page,
        "classification": row["classification"],
        "primary_text": (primary or {}).get("text"),
        "primary_bbox": (primary or {}).get("bbox"),
        "pp_text": (pp or {}).get("text"),
        "pp_bbox": (pp or {}).get("bbox"),
        "pp_score": (pp or {}).get("score"),
        "pp_line_id": (pp or {}).get("line_id"),
        "alignment_score": row.get("alignment_score"),
        "region_class": row.get("region_class"),
        "region_ids": row.get("region_ids") or [],
        "noise_reason": row.get("noise_reason"),
        "anchor_bbox": anchor.get("bbox"),
        "source_pdf": doc.get("canonical_rel_path"),
    }


def _render_crop(case: dict[str, Any], path: Path) -> None:
    pdf_path = ROOT / case["source_pdf"]
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(case["physical_page"] - 1)
        bbox = fitz.Rect(case["anchor_bbox"])
        clip = fitz.Rect(
            max(0, bbox.x0 - 45), max(0, bbox.y0 - 35),
            min(page.rect.width, bbox.x1 + 45), min(page.rect.height, bbox.y1 + 35),
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), clip=clip, alpha=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(path)
        case["crop_path"] = path.relative_to(ROOT).as_posix()
        case["crop_clip_bbox"] = [clip.x0, clip.y0, clip.x1, clip.y1]
    finally:
        doc.close()


def _render_contact_sheets(cases: list[dict[str, Any]], crop_dir: Path) -> None:
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 16) if font_path.exists() else ImageFont.load_default()
    for sheet_no in range(0, len(cases), 10):
        batch = cases[sheet_no:sheet_no + 10]
        canvas = Image.new("RGB", (2400, 900), "white")
        draw = ImageDraw.Draw(canvas)
        for offset, case in enumerate(batch):
            col, row = offset % 2, offset // 2
            x, y = col * 1200, row * 180
            crop = Image.open(ROOT / case["crop_path"]).convert("RGB")
            crop.thumbnail((560, 125))
            canvas.paste(crop, (x + 620, y + 42))
            label = (
                f"{case['calibration_id']} {case['classification']} "
                f"{case['document_sha256'][:12]} p{case['physical_page']}\n"
                f"OCR『{case.get('primary_text') or ''}』\n"
                f"PP『{case.get('pp_text') or ''}』\n"
                f"region={case.get('region_class')} noise={case.get('noise_reason')}"
            )
            draw.multiline_text((x + 8, y + 8), label, fill="black", font=font, spacing=3)
        target = crop_dir / f"contact_{sheet_no // 10 + 1:02d}.png"
        canvas.save(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-version", default=CORPUS)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--sample-size", type=int, default=60)
    args = parser.parse_args()

    run_dir = ROOT / "data/model_runtime/independent_ocr/runs" / args.run_id
    manifest, manifest_sha = load_independent_ocr_run(str(run_dir))
    pi_path = ROOT / "data/canonical" / f"{args.corpus_version}-documents.jsonl"
    docs = _read_jsonl(pi_path)
    excluded = _excluded_pages()
    per_doc_counts: dict[str, Counter[str]] = {}
    per_doc_regions: dict[str, Counter[str]] = {}
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    checked_pages = 0

    for doc in docs:
        sha = doc["sha256"]
        counts: Counter[str] = Counter()
        regions: Counter[str] = Counter()
        routed_pages = 0
        for page in doc.get("pages") or []:
            if page.get("extraction_route") not in {"full_ocr", "hybrid"}:
                continue
            routed_pages += 1
            pno = int(page["physical_page"])
            pp_page = load_independent_ocr_page(
                str(run_dir), sha, pno,
                page_width=float(page.get("width") or 0),
                page_height=float(page.get("height") or 0),
            )
            checked_pages += 1
            rows = align_page(page.get("elements") or [], pp_page.get("lines") or [], page.get("regions") or [])
            for row in rows:
                counts[row["classification"]] += 1
                if row["classification"] == "pp_only":
                    regions[row.get("region_class") or "none"] += 1
                    if row.get("noise_reason"):
                        regions[f"noise:{row['noise_reason']}"] += 1
                if (row["classification"] in {"differ", "pp_only"}
                        and (sha, pno) not in excluded):
                    candidates[sha].append(_serialise_row(row, doc, pno))
        if routed_pages:
            per_doc_counts[sha] = counts
            per_doc_regions[sha] = regions

    rng = random.Random(SEED)
    for rows in candidates.values():
        rng.shuffle(rows)
    selected: list[dict[str, Any]] = []
    doc_order = sorted(candidates)
    # Round-robin is deterministic and keeps small OCR documents represented.
    while len(selected) < args.sample_size:
        progress = False
        for sha in doc_order:
            if candidates[sha] and len(selected) < args.sample_size:
                selected.append(candidates[sha].pop())
                progress = True
        if not progress:
            break

    crop_dir = ROOT / "eval/audits/ocr_fix/T5_calibration_crops"
    calibration_path = ROOT / "eval/audits/ocr_fix/T5_calibration.jsonl"
    judgments_path = ROOT / "eval/audits/ocr_fix/T5_calibration_judgments.json"
    judgments = {}
    review_type = None
    if judgments_path.is_file():
        judgment_doc = json.loads(judgments_path.read_text(encoding="utf-8"))
        judgments = judgment_doc.get("items") or {}
        review_type = judgment_doc.get("review_type")
    lines: list[str] = []
    for idx, case in enumerate(selected, 1):
        case["calibration_id"] = f"T5C{idx:03d}"
        crop_path = crop_dir / f"T5C{idx:03d}__{case['document_sha256'][:12]}__p{case['physical_page']:04d}.png"
        _render_crop(case, crop_path)
        judgment = judgments.get(case["calibration_id"])
        if judgment:
            case["judgment"], case["original_text"] = judgment
            case["review_type"] = review_type
            case["evidence_text"] = (
                f"OCR『{case.get('primary_text') or '∅'}』 vs "
                f"PP『{case.get('pp_text') or '∅'}』 vs "
                f"原图『{case['original_text']}』"
            )
        else:
            case["judgment"] = "pending_model_review"
            case["original_text"] = None
            case["evidence_text"] = None
        lines.append(json.dumps(case, ensure_ascii=False, sort_keys=True))
    _render_contact_sheets(selected, crop_dir)
    calibration_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    total_counts = sum(per_doc_counts.values(), Counter())
    total_regions = sum(per_doc_regions.values(), Counter())
    judgment_counts = Counter(c.get("judgment") for c in selected)
    rule_results: Counter[str] = Counter()
    rule_actions: Counter[str] = Counter()
    for case in selected:
        aligned = {
            "classification": case["classification"],
            "primary": ({"text": case.get("primary_text"), "bbox": case.get("primary_bbox")}
                        if case.get("primary_bbox") is not None else None),
            "pp": ({"text": case.get("pp_text"), "bbox": case.get("pp_bbox"),
                    "score": case.get("pp_score")}
                   if case.get("pp_bbox") is not None else None),
            "alignment_score": case.get("alignment_score"),
            "region_class": case.get("region_class"),
            "noise_reason": case.get("noise_reason"),
        }
        action = fusion_action(aligned)
        if action:
            rule_actions[action] += 1
            rule_results[case["judgment"]] += 1
    stats = {
        "schema_version": "t5-alignment-stats-v1",
        "corpus_version": args.corpus_version,
        "page_intermediate_sha256": __import__("hashlib").sha256(pi_path.read_bytes()).hexdigest(),
        "independent_ocr_run_id": args.run_id,
        "run_manifest_sha256": manifest_sha,
        "run_input_page_intermediate_sha256": (manifest.get("input_hashes") or {}).get("page_intermediate"),
        "current_ocr_document_count": len(per_doc_counts),
        "checked_ocr_page_count": checked_pages,
        "excluded_t1_page_count": len(excluded),
        "calibration_seed": SEED,
        "calibration_size": len(selected),
        "calibration_judgments": dict(sorted(judgment_counts.items())),
        "calibrated_rule_sample_actions": dict(sorted(rule_actions.items())),
        "calibrated_rule_sample_judgments": dict(sorted(rule_results.items())),
        "totals": dict(sorted(total_counts.items())),
        "pp_only_breakdown": dict(sorted(total_regions.items())),
        "documents": [
            {
                "document_sha256": sha,
                "counts": dict(sorted(per_doc_counts[sha].items())),
                "pp_only_breakdown": dict(sorted(per_doc_regions[sha].items())),
            }
            for sha in sorted(per_doc_counts)
        ],
    }
    out = ROOT / "eval/audits/ocr_fix/T5_alignment_stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "documents": len(per_doc_counts), "pages": checked_pages,
        "totals": dict(total_counts), "pp_only": dict(total_regions),
        "calibration": len(selected), "stats": str(out.relative_to(ROOT)),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
