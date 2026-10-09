"""Recompute T3 TOC/section-path evidence without writing Canonical artifacts."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.application.canonical_assembly import assemble_document, load_formula_pages
from src.application.canonical_validation import apply_computed_ids, compute_ids, validate_document
from src.application.v2_chunker import _format_section_path, chunk_document
from src.evaluation.ocr_sampling import is_scanned_document
from src.ingestion.heading_hierarchy import HeadingInput, detect_toc_pages


CORPUS = "corpus-0b9a1fd84e3d"
FORMULA_RUN = "5d3264515515e228"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _toc_pages(page_doc: dict[str, Any]) -> set[int]:
    items: list[HeadingInput] = []
    index = 0
    for page in page_doc.get("pages") or []:
        for element in page.get("elements") or []:
            text = str(element.get("text") or "").strip()
            bbox = element.get("bbox") or [0, 0, 0, 0]
            if text:
                items.append(HeadingInput(text=text, bbox=bbox, source_index=index,
                                          physical_page=int(page["physical_page"])))
                index += 1
    return detect_toc_pages(items)


def _entry(entry: dict[str, Any]) -> tuple[str, str]:
    return str(entry.get("label") or ""), str(entry.get("title") or "")


def _heading_carriers(canonical: dict[str, Any]) -> tuple[dict[str, int], list[tuple[int, tuple[str, str], int]]]:
    """Recover the nearest source element for each materialized path entry."""
    element_positions: dict[str, int] = {}
    carriers: list[tuple[int, tuple[str, str], int]] = []
    for position, element in enumerate(canonical.get("elements") or []):
        element_positions[str(element["element_id"])] = position
        path = element.get("section_path") or []
        spans = element.get("source_spans") or []
        if element.get("role") in {"heading", "clause"} and element.get("label") and path and spans:
            last = _entry(path[-1])
            if last[0] == str(element["label"]):
                carriers.append((position, last, int(spans[0]["physical_page"])))
    return element_positions, carriers


def _contaminated(canonical: dict[str, Any], chunks: list[Any], toc_pages: set[int]) -> int:
    positions, carriers = _heading_carriers(canonical)
    contaminated = 0
    for chunk in chunks:
        target = min((positions[eid] for eid in chunk.element_ids if eid in positions), default=-1)
        bad = False
        for entry in chunk.metadata.get("section_path") or []:
            signature = _entry(entry)
            sources = [row for row in carriers if row[0] <= target and row[1] == signature]
            if sources and max(sources, key=lambda row: row[0])[2] in toc_pages:
                bad = True
                break
        contaminated += int(bad)
    return contaminated


def _union_by_page(chunk: Any) -> dict[int, list[float]]:
    grouped: dict[int, list[list[float]]] = {}
    for span in chunk.source_spans:
        grouped.setdefault(int(span.physical_page), []).append(list(span.bbox))
    return {page: [min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)]
            for page, boxes in grouped.items()}


def _intersection_ratio(a: list[float], b: list[float]) -> float:
    area = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    if not area:
        return 0.0
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return inter / area


def main() -> None:
    page_docs = _load_jsonl(ROOT / "data/canonical" / f"{CORPUS}-documents.jsonl")
    samples = _load_jsonl(ROOT / "eval/audits/ocr_sampling/sample_sheet.jsonl")
    judgments = {row["sample_id"]: row for row in _load_jsonl(
        ROOT / "eval/audits/ocr_sampling/judgments_codex.jsonl")}
    samples_by_sha: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        samples_by_sha.setdefault(sample["document_sha256"], []).append(sample)

    formula_dir = ROOT / "data/model_runtime/corpus_formulas" / FORMULA_RUN
    document_rows = []
    sample_rows = []
    totals = Counter()
    for page_doc in page_docs:
        sha = page_doc["sha256"]
        old_path = ROOT / "data/canonical/v2" / CORPUS / f"{sha}.canonical.json"
        old = json.loads(old_path.read_text(encoding="utf-8"))
        old_chunks = chunk_document(old)
        formula_pages, _failures = load_formula_pages(
            sha, range(1, len(page_doc.get("pages") or []) + 1), cache_dir=str(formula_dir))
        new = assemble_document(page_doc, formula_pages=formula_pages)
        apply_computed_ids(new, compute_ids(new))
        validate_document(new)
        new_chunks = chunk_document(new)

        toc_pages = _toc_pages(page_doc)
        old_bad = _contaminated(old, old_chunks, toc_pages)
        new_bad = _contaminated(new, new_chunks, toc_pages)
        scanned = is_scanned_document(old_chunks)
        old_ids = {chunk.chunk_id for chunk in old_chunks}
        new_ids = {chunk.chunk_id for chunk in new_chunks}
        changed = len(old_ids - new_ids)
        row = {
            "document_name": page_doc.get("file_name"), "document_sha256": sha,
            "scanned": scanned, "toc_pages": sorted(toc_pages),
            "contaminated_chunks_before": old_bad,
            "contaminated_chunks_after": new_bad,
            "chunks_before": len(old_chunks), "chunks_after": len(new_chunks),
            "old_chunk_ids_removed": changed,
            "new_chunk_ids_added": len(new_ids - old_ids),
        }
        document_rows.append(row)
        totals["documents"] += 1
        totals["scanned_documents"] += int(scanned)
        totals["contaminated_before"] += old_bad
        totals["contaminated_after"] += new_bad
        totals["old_chunk_ids_removed"] += changed
        totals["new_chunk_ids_added"] += len(new_ids - old_ids)
        if scanned:
            totals["scanned_contaminated_before"] += old_bad
            totals["scanned_contaminated_after"] += new_bad

        new_boxes = [(chunk, _union_by_page(chunk)) for chunk in new_chunks]
        for sample in samples_by_sha.get(sha, []):
            scores = []
            for chunk, boxes in new_boxes:
                score = max((_intersection_ratio(sample_box["bbox"], boxes.get(
                    int(sample_box["physical_page"]), [0, 0, 0, 0]))
                    for sample_box in sample["page_bboxes"]), default=0.0)
                if score > 0:
                    scores.append((score, chunk))
            scores.sort(key=lambda pair: (-pair[0], pair[1].chunk_id))
            matched = scores[0][1] if scores else None
            after = (_format_section_path(matched.metadata.get("section_path") or []).rstrip("\n")
                     if matched else None)
            old_wrong = judgments.get(sample["sample_id"], {}).get("heading_path") == "wrong"
            sample_rows.append({
                "sample_id": sample["sample_id"], "document_name": sample["document_name"],
                "physical_pages": sample["physical_pages"], "old_heading_path": sample["heading_path_text"],
                "new_heading_path": after, "bbox_overlap_ratio": round(scores[0][0], 6) if scores else 0,
                "old_heading_wrong": old_wrong,
                "heading_changed": after != sample["heading_path_text"] if matched else None,
            })

    wrong = [row for row in sample_rows if row["old_heading_wrong"]]
    result = {
        "schema_version": "t3-toc-recompute-v1", "corpus_version": CORPUS,
        "totals": dict(totals),
        "sample_matching": {
            "samples": len(sample_rows), "matched": sum(r["new_heading_path"] is not None for r in sample_rows),
            "old_heading_wrong": len(wrong),
            "old_wrong_heading_changed": sum(bool(r["heading_changed"]) for r in wrong),
            "old_wrong_heading_unchanged": sum(r["heading_changed"] is False for r in wrong),
        },
        "documents": document_rows, "samples": sample_rows,
    }
    output = ROOT / "eval/audits/ocr_fix/T3_toc_recompute.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"totals": result["totals"], "sample_matching": result["sample_matching"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
