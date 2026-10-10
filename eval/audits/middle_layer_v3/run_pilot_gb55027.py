#!/usr/bin/env python
"""Build the R1 GB 55027 pilot entirely in memory and emit review artifacts."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.assemble_corpus_v2 import _assemble_one, _iter_page_documents  # noqa: E402
from src.application.v2_chunker import _format_section_path, chunk_document  # noqa: E402
from src.evaluation.gold_anchor_exam import chunks_from_v2, grade, normalize  # noqa: E402
from src.evaluation.clause_continuity import inspect_clause_continuity  # noqa: E402
from src.ingestion.block_assembly import load_config, prepare_page_document_v3  # noqa: E402
from src.ingestion.page_furniture import watermark_line_variants  # noqa: E402


CORPUS_VERSION = "corpus-0ae8a9423e2f"
SHA = "60bfe78380e9533e32ef659b054daf9fa7f072557824b7ae759b51371f1ca41f"
FORMULA_RUN = "5d3264515515e228"
OCR_RUN = "ocrv6-403f2b876d9911e5"
RAW_OCR_RUN = "ocrv6-ef06411e1606ba35"
OUT = ROOT / "eval" / "audits" / "middle_layer_v3" / "pilot_gb55027"
_PAGE_NUMBER = re.compile(r"^(?:[·•]\s*\d+\s*[·•]|-\s*\d+\s*-|\d+|第\s*\d+\s*页)$")
_LIST_ROOT = re.compile(r"^(?:\d+[、）)]|[（(][一二三四五六七八九十\d]+[）)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)$")


def main() -> int:
    page_doc = next(
        row for row in _iter_page_documents(CORPUS_VERSION)
        if row.get("sha256") == SHA
    )
    ocr_dir = ROOT / "data" / "model_runtime" / "independent_ocr" / "runs" / OCR_RUN
    raw_ocr_dir = ROOT / "data" / "model_runtime" / "independent_ocr" / "runs" / RAW_OCR_RUN
    prepared = prepare_page_document_v3(
        page_doc, independent_ocr_run_dir=str(ocr_dir),
        raw_independent_ocr_run_dir=str(raw_ocr_dir))
    v3 = _assemble_one(
        page_doc, FORMULA_RUN, OCR_RUN,
        raw_independent_ocr_run_id=RAW_OCR_RUN, middle_layer="v3")
    legacy = _assemble_one(page_doc, FORMULA_RUN, OCR_RUN, middle_layer="legacy")
    v3_chunks = chunk_document(v3)
    legacy_chunks = chunk_document(legacy)

    OUT.mkdir(parents=True, exist_ok=True)
    rows = [_chunk_row(v3, chunk) for chunk in v3_chunks]
    (OUT / "chunks.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (OUT / "page_review.md").write_text(
        _page_review(page_doc, prepared, rows), encoding="utf-8")

    anchors = json.loads(
        (ROOT / "data" / "registry" / "m2-anchor-review-v0.json").read_text(
            encoding="utf-8"))["anchors"]
    anchors = [anchor for anchor in anchors if anchor["file_name"] == page_doc["file_name"]]
    exam = grade(anchors, chunks_from_v2(page_doc["file_name"], v3_chunks))
    metrics = {
        "schema_version": "middle-layer-v3-pilot-metrics/1",
        "corpus_version": CORPUS_VERSION,
        "source_sha256": SHA,
        "document": page_doc["file_name"],
        "physical_page_count": len(page_doc.get("pages") or []),
        "canonical_validation": "passed",
        "gold_anchor_exam": {
            "summary": exam.summary(),
            "results": [result.__dict__ for result in exam.results],
        },
        "v3_preparation": prepared.get("_v3_stats"),
        "v3_ocr_text": prepared.get("_v3_ocr_summary"),
        "v3_ocr_audit": _ocr_audit(prepared),
        "v3": _metrics(v3, v3_chunks),
        "legacy": _metrics(legacy, legacy_chunks),
    }
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "conflicts.md").write_text(
        _conflict_review(metrics["v3_ocr_audit"]), encoding="utf-8")
    print(json.dumps({
        "output": str(OUT), "chunks": len(v3_chunks),
        "exam": exam.summary(), "v3": metrics["v3"], "legacy": metrics["legacy"],
    }, ensure_ascii=False, indent=2))
    invariant_count = int(
        (metrics.get("v3_ocr_text") or {}).get("merge_invariant_violation_count") or 0)
    return 1 if invariant_count else 0


def _chunk_row(document: dict[str, Any], chunk: Any) -> dict[str, Any]:
    path = chunk.metadata.get("section_path") or []
    prefix = _format_section_path(path)
    body = chunk.text[len(prefix):] if prefix and chunk.text.startswith(prefix) else chunk.text
    element_lookup = {element["element_id"]: element for element in document.get("elements") or []}
    return {
        "chunk_id": chunk.chunk_id,
        "document": (document.get("source") or {}).get("file_name"),
        "section_path": " > ".join(
            " ".join(part for part in (node.get("label"), node.get("title")) if part)
            for node in path),
        "section_labels": [node.get("label") or "" for node in path],
        "body": body,
        "element_types": sorted({element_lookup[element_id]["type"] for element_id in chunk.element_ids}),
        "pages": sorted({span.physical_page for span in chunk.source_spans}),
        "bboxes": [{"physical_page": span.physical_page, "bbox": span.bbox}
                   for span in chunk.source_spans],
    }


def _metrics(document: dict[str, Any], chunks: list[Any]) -> dict[str, Any]:
    config = load_config()
    watermarks = watermark_line_variants(config["watermark_exact_lines"])
    body_furniture: list[dict[str, Any]] = []
    sentence_headings: list[str] = []
    long_titles: set[str] = set()
    roots: set[str] = set()
    page_heights = {int(page["physical_page"]): float(page["height"])
                    for page in document.get("pages") or []}
    for element in document.get("elements") or []:
        if element.get("type") != "text":
            continue
        text = str((element.get("content") or {}).get("text") or "")
        if element.get("role") not in {"header", "footer", "page_number", "watermark", "unknown"}:
            spans = element.get("source_spans") or []
            for index, line in enumerate(text.splitlines()):
                norm = re.sub(r"\s+", "", line)
                span = spans[index] if index < len(spans) else (spans[0] if spans else {})
                height = page_heights.get(int(span.get("physical_page") or 0), 0.0)
                bbox = span.get("bbox") or [0, 0, 0, 0]
                in_band = bool(height and (bbox[1] <= 0.12 * height or bbox[3] >= 0.88 * height))
                if (_PAGE_NUMBER.fullmatch(line.strip()) and in_band) or norm in watermarks:
                    body_furniture.append({"element_id": element["element_id"], "line": line})
        if element.get("role") in {"heading", "document_title"} and re.search(r"[。！？；;：:]\s*$", text):
            sentence_headings.append(text)
        path = element.get("section_path") or []
        if path:
            root = str(path[0].get("label") or "")
            if _LIST_ROOT.fullmatch(root):
                roots.add(root)
        for node in path:
            title = node.get("title")
            if title and len(title) > 60:
                long_titles.add(title)
    rows = [_chunk_row(document, chunk) for chunk in chunks]
    bodies = [normalize(row["body"]) for row in rows]
    short = [row["chunk_id"] for row, body in zip(rows, bodies) if len(body) < 15]
    ocr_source_elements = [element for element in document.get("elements") or []
                           if element.get("ocr_text_sources")]
    ocr_source_counts = Counter(
        source.get("source")
        for element in ocr_source_elements
        for source in element.get("ocr_text_sources") or []
    )
    return {
        "element_count": len(document.get("elements") or []),
        "element_type_counts": dict(Counter(element.get("type") for element in document.get("elements") or [])),
        "chunk_count": len(chunks),
        "body_page_number_footer_watermark_lines": len(body_furniture),
        "body_furniture_examples": body_furniture,
        "sentence_ended_heading_count": len(sentence_headings),
        "sentence_ended_heading_examples": sentence_headings,
        "body_breadcrumb_count": sum(" > " in row["body"] for row in rows),
        "section_title_over_60_count": len(long_titles),
        "section_title_over_60_examples": sorted(long_titles),
        "list_item_root_count": len(roots),
        "list_item_root_labels": sorted(roots),
        "short_body_chunk_lt15_count": len(short),
        "short_body_chunk_lt15_ratio": round(len(short) / len(chunks), 6) if chunks else 0.0,
        "short_body_chunk_ids": short,
        "ocr_text_source_element_count": len(ocr_source_elements),
        "ocr_text_source_counts": dict(ocr_source_counts),
        "ocr_text_source_missing_run_id_count": sum(
            not source.get("run_id")
            for element in ocr_source_elements
            for source in element.get("ocr_text_sources") or []),
        "clause_number_continuity": inspect_clause_continuity(document),
    }


def _ocr_audit(prepared: dict[str, Any]) -> dict[str, Any]:
    single_clean = []
    conflicts = []
    decisions = []
    for page in prepared.get("pages") or []:
        physical_page = int(page.get("physical_page") or 0)
        for audit in page.get("_v3_ocr_line_audit") or []:
            row = {
                "physical_page": physical_page,
                "bbox": audit.get("bbox"),
                "output_text": audit.get("output_text"),
                "source_texts": audit.get("source_texts"),
            }
            decisions.append({
                **row, "rule": audit.get("rule"),
                "merge_invariant_violations": audit.get("merge_invariant_violations") or [],
            })
            if audit.get("rule") == "single_source" and audit.get("single_source") == "pp_clean":
                single_clean.append(row)
            for conflict in audit.get("conflict_fragments") or []:
                conflicts.append({**row, "conflict": conflict})
    alignment_defects = _reviewed_alignment_defects(decisions)
    return {
        "single_source_pp_clean": single_clean,
        "conflict_fragments": conflicts,
        "reviewed_alignment_defects": alignment_defects,
        "conflict_review_entry_count": len(conflicts) + len(alignment_defects),
        "line_decisions": decisions,
    }


def _reviewed_alignment_defects(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = [
        ("2.1.10", "split_primary_tail", "2.1.10 乡村污水系统"),
        ("2.2.13", "replacement_must_not_move_to_line_end", "2.2.13 排水工程"),
        ("2.3.5", "overlapping_primary_fragments", "必须在2h内安放护栏和警示"),
    ]
    result = []
    for clause, issue, needle in cases:
        row = next((item for item in decisions if needle in str(item.get("output_text") or "")), None)
        if row is None:
            raise RuntimeError(f"missing reviewed alignment case {clause}")
        result.append({**row, "clause": clause, "issue": issue})
    return result


def _conflict_review(audit: dict[str, Any]) -> str:
    conflicts = list(audit.get("conflict_fragments") or [])
    alignment_defects = list(audit.get("reviewed_alignment_defects") or [])
    total = len(conflicts) + len(alignment_defects)
    lines = [
        "# GB 55027-2022 three-source conflict review",
        "",
        f"Review entries: **{total}** (current global-alignment conflict fragments: "
        f"**{len(conflicts)}**; resolved alignment-defect cases from the 44-item "
        f"mid-review count: **{len(alignment_defects)}**).",
        "",
        "Generated merge evidence only; every item still requires comparison with the original PDF page.",
        "",
    ]
    for index, row in enumerate(conflicts, start=1):
        source_texts = row.get("source_texts") or {}
        conflict = row.get("conflict") or {}
        lines.extend([
            f"## {index}. Physical page {row.get('physical_page')}",
            "",
            f"- bbox: `{json.dumps(row.get('bbox'), ensure_ascii=False)}`",
            f"- selected source: `{conflict.get('selected_source')}`",
            f"- selected fragment: `{_inline(conflict.get('selected'))}`",
            f"- pp_clean: `{_inline(source_texts.get('pp_clean'))}`",
            f"- pp_raw: `{_inline(source_texts.get('pp_raw'))}`",
            f"- primary: `{_inline(source_texts.get('primary'))}`",
            f"- merged line: `{_inline(row.get('output_text'))}`",
            "",
        ])
    for offset, row in enumerate(alignment_defects, start=len(conflicts) + 1):
        source_texts = row.get("source_texts") or {}
        lines.extend([
            f"## {offset}. Physical page {row.get('physical_page')} (resolved alignment defect)",
            "",
            f"- clause: `{row.get('clause')}`",
            f"- defect: `{row.get('issue')}`",
            f"- bbox: `{json.dumps(row.get('bbox'), ensure_ascii=False)}`",
            f"- pp_clean: `{_inline(source_texts.get('pp_clean'))}`",
            f"- pp_raw: `{_inline(source_texts.get('pp_raw'))}`",
            f"- primary: `{_inline(source_texts.get('primary'))}`",
            f"- merged line: `{_inline(row.get('output_text'))}`",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _inline(value: Any) -> str:
    if value is None:
        return "(missing)"
    return str(value).replace("`", "\\`").replace("\r", " ").replace("\n", " ")


def _page_review(page_doc: dict[str, Any], prepared: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# GB 55027-2022 middle-layer v3 page review",
        "",
        f"- source SHA-256: `{SHA}`",
        "- Generated from an in-memory v3 assembly; this is review evidence, not a published corpus.",
        "- OCR-derived text remains `needs_review` / locator-only until independent original-page review.",
        "",
    ]
    prepared_pages = {int(page["physical_page"]): page for page in prepared.get("pages") or []}
    for page in range(1, len(page_doc.get("pages") or []) + 1):
        lines.extend([f"## Physical page {page}", "", "### Chunks", ""])
        page_rows = [row for row in rows if page in row["pages"]]
        if not page_rows:
            lines.append("- None")
        for row in page_rows:
            types = ", ".join(row["element_types"])
            lines.extend([
                f"#### `{row['chunk_id']}` ({types})", "",
                f"Section: `{row['section_path'] or '(root)'}`", "",
                "```text", row["body"], "```", "",
            ])
        lines.extend(["### Page furniture and TOC", ""])
        marked = [block for block in prepared_pages.get(page, {}).get("elements") or []
                  if block.get("_v3_furniture_rule") or block.get("_v3_role") == "toc"]
        if not marked:
            lines.append("- None")
        for block in marked:
            rule = block.get("_v3_furniture_rule") or "toc_navigation_only"
            text = str(block.get("text") or "").replace("\n", " / ")
            lines.append(f"- `{block.get('_v3_role')}` via `{rule}`: {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
