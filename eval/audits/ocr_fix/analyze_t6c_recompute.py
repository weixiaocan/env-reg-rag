#!/usr/bin/env python
"""T6c in-memory recompute and before/after audit.

The script reads the immutable page-intermediate candidate and existing local
formula/independent-OCR caches.  It never writes Canonical, registry, chunk,
Qdrant, or publish artifacts; its only output is the audit JSON named below.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.application.canonical_assembly import assemble_document, load_formula_pages  # noqa: E402
from src.application.canonical_validation import apply_computed_ids, compute_ids, validate_document  # noqa: E402
from src.application import v2_chunker  # noqa: E402
from src.application.v2_chunker import _format_section_path, chunk_document  # noqa: E402

CORPUS = "corpus-966c89b264cc"
FORMULA_RUN = "5d3264515515e228"
OCR_RUN = "ocrv6-ef06411e1606ba35"
OUTPUT = ROOT / "eval/audits/ocr_fix/T6c_recompute.json"

TARGET_SAMPLE_IDS = {
    "ocr-c3c3df5ae9ca-009", "ocr-c3c3df5ae9ca-016",
    "ocr-c3c3df5ae9ca-018", "ocr-c3c3df5ae9ca-019",
    "ocr-c3c3df5ae9ca-024", "ocr-fd10712cb0f7-004",
    "ocr-fd10712cb0f7-008", "ocr-fd10712cb0f7-016",
    "ocr-fd10712cb0f7-017", "ocr-fd10712cb0f7-024",
    "ocr-60bfe78380e9-005", "ocr-60bfe78380e9-011",
    "ocr-60bfe78380e9-024", "ocr-4336797dedd1-011",
    "ocr-4336797dedd1-024", "ocr-adf038d73f82-005",
    "ocr-adf038d73f82-022", "ocr-adf038d73f82-023",
    "ocr-1e4f99290e1e-013", "ocr-1e4f99290e1e-024",
}

_SENTENCE_END_RE = re.compile(r"[。！？；;：:]\s*$")
_ROOT_BODY_VERB_RE = re.compile(r"(?:应|宜|不得|不应|必须|可|按下式|符合下列|有关规定执行)")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _assemble(page_doc: dict[str, Any]) -> dict[str, Any]:
    sha = page_doc["sha256"]
    formula_dir = ROOT / "data/model_runtime/corpus_formulas" / FORMULA_RUN
    formula_pages, failures = load_formula_pages(
        sha, range(1, len(page_doc.get("pages") or []) + 1),
        cache_dir=str(formula_dir),
    )
    if failures:
        raise AssertionError(f"formula cache failures for {sha}: {failures}")
    run_dir = ROOT / "data/model_runtime/independent_ocr/runs" / OCR_RUN
    doc = assemble_document(
        copy.deepcopy(page_doc), formula_pages=copy.deepcopy(formula_pages),
        independent_ocr_run_dir=str(run_dir),
    )
    apply_computed_ids(doc, compute_ids(doc))
    validate_document(doc)
    return doc


def _legacy_prefix_for(builder: Any, section_path: list[dict[str, Any]]) -> str:
    """Exact pre-T6c labels-only prefix cache, for before evidence only."""
    key = " | ".join((entry.get("label") or "") for entry in section_path)
    if key not in builder._prefix_cache:
        builder._prefix_cache[key] = _format_section_path(section_path)
    return builder._prefix_cache[key]


def _chunk_body(chunk: Any) -> str:
    prefix = _format_section_path(chunk.metadata.get("section_path") or [])
    return chunk.text[len(prefix):] if prefix and chunk.text.startswith(prefix) else chunk.text


def _source_span_overlap(old_chunk: Any, new_chunk: Any) -> float:
    new_by_page: dict[int, list[list[float]]] = defaultdict(list)
    for span in new_chunk.source_spans:
        new_by_page[int(span.physical_page)].append(list(span.bbox))
    covered = 0.0
    total = 0.0
    for span in old_chunk.source_spans:
        box = list(span.bbox)
        area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
        total += area
        covered += max((
            max(0.0, min(box[2], candidate[2]) - max(box[0], candidate[0]))
            * max(0.0, min(box[3], candidate[3]) - max(box[1], candidate[1]))
            for candidate in new_by_page.get(int(span.physical_page), [])
        ), default=0.0)
    return covered / total if total else 0.0


def _match_chunk(old_chunk: Any, new_chunks: list[Any]) -> tuple[float, Any | None]:
    matches = [(_source_span_overlap(old_chunk, chunk), chunk) for chunk in new_chunks]
    matches = [row for row in matches if row[0] > 0]
    matches.sort(key=lambda row: (-row[0], row[1].chunk_id))
    return matches[0] if matches else (0.0, None)


def _match_element(old_chunk: Any, new_doc: dict[str, Any]) -> tuple[float, dict[str, Any] | None]:
    old_boxes = [(int(span.physical_page), list(span.bbox))
                 for span in old_chunk.source_spans]
    matches: list[tuple[float, dict[str, Any]]] = []
    for element in new_doc.get("elements") or []:
        new_by_page: dict[int, list[list[float]]] = defaultdict(list)
        for span in element.get("source_spans") or []:
            new_by_page[int(span["physical_page"])].append(list(span["bbox"]))
        covered = 0.0
        total = 0.0
        for page, box in old_boxes:
            area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
            total += area
            covered += max((
                max(0.0, min(box[2], candidate[2]) - max(box[0], candidate[0]))
                * max(0.0, min(box[3], candidate[3]) - max(box[1], candidate[1]))
                for candidate in new_by_page.get(page, [])
            ), default=0.0)
        score = covered / total if total else 0.0
        if score > 0:
            matches.append((score, element))
    matches.sort(key=lambda row: (-row[0], row[1]["element_id"]))
    return matches[0] if matches else (0.0, None)


def _looks_like_item_root(entry: dict[str, Any]) -> bool:
    label = str(entry.get("label") or "")
    title = str(entry.get("title") or "")
    if not label.isdigit() or not title:
        return False
    text = f"{label} {title}"
    return bool(_SENTENCE_END_RE.search(text)
                or (len(title) > 8 and _ROOT_BODY_VERB_RE.search(title)))


def _ocr_line_average(doc: dict[str, Any]) -> tuple[int, int, float]:
    ocr_pages = {
        int(page["physical_page"])
        for page in doc.get("pages") or []
        if page.get("extraction_route") in {"full_ocr", "hybrid"}
    }
    elements = 0
    lines = 0
    for element in doc.get("elements") or []:
        if element.get("type") != "text":
            continue
        spans = [span for span in element.get("source_spans") or []
                 if int(span.get("physical_page") or 0) in ocr_pages
                 and span.get("role") in {"primary", "continuation"}]
        if spans:
            elements += 1
            lines += len(spans)
    return elements, lines, round(lines / elements, 6) if elements else 0.0


def _half_sentence_chunk_count(doc: dict[str, Any], chunks: list[Any]) -> int:
    text_elements = [element for element in doc.get("elements") or []
                     if element.get("type") == "text"]
    positions = {element["element_id"]: index
                 for index, element in enumerate(text_elements)}
    count = 0
    for chunk in chunks:
        body = _chunk_body(chunk).rstrip()
        if not body or _SENTENCE_END_RE.search(body) or not chunk.element_ids:
            continue
        index = positions.get(chunk.element_ids[-1])
        if index is None or index + 1 >= len(text_elements):
            continue
        current = text_elements[index]
        following = text_elements[index + 1]
        if current.get("section_path") == following.get("section_path"):
            count += 1
    return count


def _metrics(doc: dict[str, Any], chunks: list[Any]) -> dict[str, Any]:
    item_roots = {
        (entry.get("label"), entry.get("title"))
        for element in doc.get("elements") or []
        for entry in (element.get("section_path") or [])[:1]
        if _looks_like_item_root(entry)
    }
    punctuated_headings = sum(
        element.get("type") == "text"
        and element.get("role") == "heading"
        and bool(_SENTENCE_END_RE.search(
            str((element.get("content") or {}).get("text") or "")))
        for element in doc.get("elements") or []
    )
    breadcrumb_body_samples = [
        {"chunk_id": chunk.chunk_id, "body": _chunk_body(chunk),
         "section_path": chunk.metadata.get("section_path") or []}
        for chunk in chunks
        if chunk.metadata.get("element_types") == ["text"]
        and " > " in _chunk_body(chunk)
    ]
    ocr_elements, ocr_lines, ocr_average = _ocr_line_average(doc)
    return {
        "item_like_distinct_root_nodes": len(item_roots),
        "item_like_root_values": [f"{label} {title}" for label, title in sorted(item_roots)],
        "headings_ending_sentence_punctuation": punctuated_headings,
        "chunk_bodies_containing_breadcrumb_separator": len(breadcrumb_body_samples),
        "breadcrumb_body_samples": breadcrumb_body_samples[:10],
        "ocr_text_elements": ocr_elements,
        "ocr_source_lines": ocr_lines,
        "ocr_average_lines_per_text_element": ocr_average,
        "half_sentence_chunks_followed_by_same_section": _half_sentence_chunk_count(doc, chunks),
        "chunks": len(chunks),
    }


def _sample_result(sample: dict[str, Any], old_chunks: dict[str, Any],
                   new_doc: dict[str, Any], new_chunks: list[Any]) -> dict[str, Any]:
    old_chunk = old_chunks.get(sample["chunk_id"])
    if old_chunk is None:
        raise AssertionError(f"sample chunk absent: {sample['sample_id']} {sample['chunk_id']}")
    score, new_chunk = _match_chunk(old_chunk, new_chunks)
    element_score, new_element = _match_element(old_chunk, new_doc)
    element_text = None
    if new_element is not None:
        content = new_element.get("content") or {}
        element_text = content.get("text") or content.get("caption") or content.get("latex")
    path = (new_chunk.metadata.get("section_path") if new_chunk else
            (new_element or {}).get("section_path")) or []
    screenshots = [shot.get("context", {}).get("path")
                   for shot in sample.get("screenshots") or []]
    return {
        "sample_id": sample["sample_id"],
        "document_name": sample.get("document_name"),
        "physical_pages": sample.get("physical_pages"),
        "old_heading_path": sample.get("heading_path_text"),
        "old_body": sample.get("body_text"),
        "new_chunk_id": new_chunk.chunk_id if new_chunk else None,
        "new_element_id": new_element.get("element_id") if new_element else None,
        "new_element_role": new_element.get("role") if new_element else None,
        "new_element_text": element_text,
        "source_span_overlap_ratio": round(score, 6),
        "element_source_span_overlap_ratio": round(element_score, 6),
        "new_heading_path": _format_section_path(path).rstrip("\n") if path else None,
        "new_body": _chunk_body(new_chunk) if new_chunk else None,
        "projection_status": ("retrieval_chunk" if new_chunk else
                              "heading_prefix_only" if new_element and new_element.get("role") == "heading"
                              else "canonical_element_only" if new_element else "unmatched"),
        "context_images": screenshots,
    }


def main() -> int:
    page_docs = _read_jsonl(ROOT / "data/canonical" / f"{CORPUS}-documents.jsonl")
    samples = {row["sample_id"]: row for row in _read_jsonl(
        ROOT / "eval/audits/ocr_sampling/v2/sample_sheet.jsonl")
        if row["sample_id"] in TARGET_SAMPLE_IDS}
    missing = sorted(TARGET_SAMPLE_IDS - set(samples))
    if missing:
        raise AssertionError(f"target samples missing: {missing}")
    samples_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples.values():
        samples_by_sha[sample["document_sha256"]].append(sample)

    total_before: Counter[str] = Counter()
    total_after: Counter[str] = Counter()
    document_rows = []
    sample_rows = []
    for page_doc in page_docs:
        sha = page_doc["sha256"]
        old_path = ROOT / "data/canonical/v2" / CORPUS / f"{sha}.canonical.json"
        old_doc = json.loads(old_path.read_text(encoding="utf-8"))
        new_doc = _assemble(page_doc)
        with patch.object(v2_chunker._ChunkBuilder, "_prefix_for", _legacy_prefix_for):
            old_chunks = chunk_document(old_doc)
        new_chunks = chunk_document(new_doc)
        before = _metrics(old_doc, old_chunks)
        after = _metrics(new_doc, new_chunks)
        for key, value in before.items():
            if isinstance(value, (int, float)) and key != "ocr_average_lines_per_text_element":
                total_before[key] += value
        for key, value in after.items():
            if isinstance(value, (int, float)) and key != "ocr_average_lines_per_text_element":
                total_after[key] += value
        document_rows.append({
            "document_sha256": sha,
            "document_name": page_doc.get("file_name"),
            "before": before,
            "after": after,
        })
        old_chunk_map = {chunk.chunk_id: chunk for chunk in old_chunks}
        for sample in samples_by_sha.get(sha, []):
            sample_rows.append(_sample_result(sample, old_chunk_map, new_doc, new_chunks))

    for total in (total_before, total_after):
        elements = total["ocr_text_elements"]
        total["ocr_average_lines_per_text_element"] = round(
            total["ocr_source_lines"] / elements, 6) if elements else 0.0

    sample_rows.sort(key=lambda row: row["sample_id"])
    result = {
        "schema_version": "t6c-recompute-v1",
        "corpus_version": CORPUS,
        "method": {
            "mode": "offline in-memory",
            "writes_formal_artifacts": False,
            "formula_run_id": FORMULA_RUN,
            "independent_ocr_run_id": OCR_RUN,
            "before": "existing candidate Canonical assembled before T6c",
            "after": "same page-intermediate and caches assembled with working-tree T6c code",
            "half_sentence_definition": (
                "chunk body lacks terminal punctuation and its last text element "
                "is followed by another text element with the same section_path"
            ),
        },
        "totals": {"documents": len(document_rows),
                   "before": dict(total_before), "after": dict(total_after)},
        "samples": sample_rows,
        "documents": document_rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({
        "documents": len(document_rows),
        "samples": len(sample_rows),
        "matched_samples": sum(
            row["new_chunk_id"] is not None or row["new_element_id"] is not None
            for row in sample_rows),
        "before": dict(total_before),
        "after": dict(total_after),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
