#!/usr/bin/env python
"""In-memory T5 Canonical/chunk recompute; never writes formal artifacts."""
from __future__ import annotations

import copy
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.application.canonical_assembly import assemble_document, load_formula_pages  # noqa: E402
from src.application.canonical_validation import apply_computed_ids, compute_ids, validate_document  # noqa: E402
from src.application.v2_chunker import chunk_document  # noqa: E402
from eval.audits.ocr_fix.analyze_t4_order import _chunk_body, _source_span_overlap  # noqa: E402

CORPUS = "corpus-0b9a1fd84e3d"
FORMULA_RUN = "5d3264515515e228"
OCR_RUN = "ocrv6-ef06411e1606ba35"

KNOWN_PAGES = {
    ("c3c3df5ae9ca", 56): ["有效水深", "进水方向", "宜设置除砂"],
    ("c3c3df5ae9ca", 14): ["载体表面的生物膜"],
    ("fd10712cb0f7", 106): ["4监控量测信息", "合格后进行", "4 初期衬砌", "5 防水卷材"],
    ("fd10712cb0f7", 107): ["2 采取措施", "4 模板施工", "5 混凝土浇筑", "方可进行灌筑"],
    ("60bfe78380e9", 10): ["3.1.4", "3.1.5", "3.1.6", "城镇竖向"],
    ("f28bb600c899", 4): ["大雨、暴雨、大暴雨、特大暴雨共7个等级"],
    ("4336797dedd1", 25): ["2mg/L，不扣分", "口数量仅1处，扣4分", "口数量仅1处，扣6分", "存在一处，扣10分"],
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assemble(page_doc: dict[str, Any], formula_pages: dict[int, list[dict[str, Any]]],
              run_dir: Path | None) -> dict[str, Any]:
    doc = assemble_document(
        copy.deepcopy(page_doc), formula_pages=copy.deepcopy(formula_pages),
        independent_ocr_run_dir=str(run_dir) if run_dir else None,
    )
    apply_computed_ids(doc, compute_ids(doc))
    validate_document(doc)
    return doc


def _page_text(doc: dict[str, Any], page: int) -> list[dict[str, Any]]:
    result = []
    for element in doc.get("elements") or []:
        if element.get("type") != "text":
            continue
        spans = [s for s in element.get("source_spans") or []
                 if int(s.get("physical_page") or 0) == page]
        if spans:
            result.append({
                "text": element["content"]["text"],
                "bbox": spans[0]["bbox"],
                "fusion": element.get("ocr_fusion") or [],
            })
    return result


def main() -> int:
    docs = _read_jsonl(ROOT / "data/canonical" / f"{CORPUS}-documents.jsonl")
    samples = _read_jsonl(ROOT / "eval/audits/ocr_sampling/sample_sheet.jsonl")
    samples_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        samples_by_sha[sample["document_sha256"]].append(sample)
    formula_dir = ROOT / "data/model_runtime/corpus_formulas" / FORMULA_RUN
    run_dir = ROOT / "data/model_runtime/independent_ocr/runs" / OCR_RUN
    totals: Counter[str] = Counter()
    document_rows = []
    sample_rows = []
    known_results = []

    for page_doc in docs:
        sha = page_doc["sha256"]
        formula_pages, failures = load_formula_pages(
            sha, range(1, len(page_doc.get("pages") or []) + 1), cache_dir=str(formula_dir))
        if failures:
            raise AssertionError(f"formula cache failures for {sha}: {failures}")
        baseline = _assemble(page_doc, formula_pages, None)
        fused = _assemble(page_doc, formula_pages, run_dir)
        baseline_chunks = chunk_document(baseline)
        fused_chunks = chunk_document(fused)
        fusion_elements = [e for e in fused.get("elements") or [] if e.get("ocr_fusion")]
        actions = Counter(e["ocr_fusion"][0]["action"] for e in fusion_elements)
        document_rows.append({
            "document_sha256": sha, "document_name": page_doc.get("file_name"),
            "baseline_elements": len(baseline.get("elements") or []),
            "fused_elements": len(fused.get("elements") or []),
            "baseline_chunks": len(baseline_chunks), "fused_chunks": len(fused_chunks),
            "inserted": actions["insert"], "replaced": actions["replace"],
        })
        totals["documents"] += 1
        totals["baseline_elements"] += len(baseline.get("elements") or [])
        totals["fused_elements"] += len(fused.get("elements") or [])
        totals["baseline_chunks"] += len(baseline_chunks)
        totals["fused_chunks"] += len(fused_chunks)
        totals["inserted"] += actions["insert"]
        totals["replaced"] += actions["replace"]

        for sample in samples_by_sha.get(sha, []):
            old_published = sample["body_text"]
            old_chunk_id = sample["chunk_id"]
            published_path = ROOT / "data/canonical/v2" / CORPUS / f"{sha}.canonical.json"
            published = json.loads(published_path.read_text(encoding="utf-8"))
            published_chunks = {c.chunk_id: c for c in chunk_document(published)}
            old_chunk = published_chunks[old_chunk_id]
            def match(chunks):
                matches = [( _source_span_overlap(old_chunk, c), c) for c in chunks]
                matches = [m for m in matches if m[0] > 0]
                matches.sort(key=lambda value: (-value[0], value[1].chunk_id))
                return matches[0] if matches else (0.0, None)
            base_score, base_chunk = match(baseline_chunks)
            fused_score, fused_chunk = match(fused_chunks)
            base_body = _chunk_body(base_chunk) if base_chunk else None
            fused_body = _chunk_body(fused_chunk) if fused_chunk else None
            sample_rows.append({
                "sample_id": sample["sample_id"], "document_sha256": sha,
                "physical_pages": sample.get("physical_pages"),
                "published_body": old_published, "baseline_body": base_body,
                "fused_body": fused_body,
                "changed_vs_published": fused_body != old_published if fused_body is not None else None,
                "changed_by_t5": fused_body != base_body if fused_body is not None else None,
                "new_chunk_id": fused_chunk.chunk_id if fused_chunk else None,
                "source_span_overlap_ratio": round(fused_score, 6),
                "baseline_overlap_ratio": round(base_score, 6),
            })

        for (prefix, page), needles in KNOWN_PAGES.items():
            if not sha.startswith(prefix):
                continue
            before_lines = _page_text(baseline, page)
            after_lines = _page_text(fused, page)
            blob = "\n".join(line["text"] for line in after_lines)
            known_results.append({
                "document_sha256": sha, "physical_page": page,
                "needles": [{"text": needle, "recovered": needle in blob} for needle in needles],
                "baseline_lines": before_lines,
                "fused_lines": after_lines,
                "fused_changes": [line for line in after_lines if line["fusion"]],
            })

    result = {
        "schema_version": "t5-recompute-v1", "corpus_version": CORPUS,
        "method": {"mode": "offline in-memory", "writes_formal_artifacts": False,
                   "independent_ocr_run_id": OCR_RUN},
        "totals": dict(totals),
        "sample_matching": {
            "samples": len(sample_rows),
            "matched": sum(row["fused_body"] is not None for row in sample_rows),
            "changed_vs_published": sum(row["changed_vs_published"] is True for row in sample_rows),
            "unchanged_vs_published": sum(row["changed_vs_published"] is False for row in sample_rows),
            "changed_by_t5": sum(row["changed_by_t5"] is True for row in sample_rows),
            "unchanged_by_t5": sum(row["changed_by_t5"] is False for row in sample_rows),
        },
        "documents": document_rows, "samples": sample_rows,
        "known_results": known_results,
    }
    out = ROOT / "eval/audits/ocr_fix/T5_recompute.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"totals": result["totals"],
                      "sample_matching": result["sample_matching"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
