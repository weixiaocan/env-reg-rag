"""Measure the T4 reading-order fix without writing Canonical artifacts."""
from __future__ import annotations

import copy
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.application import canonical_assembly
from src.application.canonical_assembly import assemble_document, load_formula_pages
from src.application.canonical_validation import apply_computed_ids, compute_ids, validate_document
from src.application.v2_chunker import _format_section_path, chunk_document
from src.evaluation.ocr_sampling import is_scanned_document
from src.ingestion import column_detection as columns


CORPUS = "corpus-0b9a1fd84e3d"
FORMULA_RUN = "5d3264515515e228"
TARGET_SHA_PREFIX = "4336797dedd1"
TARGET_PAGE = 25
Y_INVERSION_TOLERANCE = 3.0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _legacy_detect_columns(
    bboxes: list[list[float]], *, page_width: float, page_height: float
) -> columns.ColumnLayout:
    """Exact pre-T4 detector, retained locally only for before/after evidence."""
    del page_height
    if not bboxes:
        return columns.ColumnLayout(column_count=1, columns=[], reading_order=[])
    valid = [i for i, box in enumerate(bboxes) if columns._is_finite_box(box)]
    if not valid:
        return columns.ColumnLayout(column_count=1, columns=[], reading_order=[])
    full, body = columns._split_full_width(bboxes, page_width)
    if len(body) < columns.MIN_FOR_TWO_COLUMN:
        split = None
    else:
        centers = sorted(columns._center_x(bboxes[i]) for i in body)
        best_gap = 0.0
        best_split = 0.0
        for left_center, right_center in zip(centers, centers[1:]):
            gap = right_center - left_center
            midpoint = (left_center + right_center) / 2.0
            if not (columns.CENTER_LO * page_width <= midpoint
                    <= columns.CENTER_HI * page_width):
                continue
            if gap > best_gap:
                best_gap = gap
                best_split = midpoint
        left_count = sum(1 for i in body
                         if columns._center_x(bboxes[i]) < best_split)
        right_count = len(body) - left_count
        split = ((best_split, best_gap)
                 if best_gap >= columns.GUTTER_MIN_RATIO * page_width
                 and left_count >= columns.MIN_PER_COLUMN
                 and right_count >= columns.MIN_PER_COLUMN else None)
    if split is None:
        order = columns._sort_by_y(valid, bboxes)
        return columns.ColumnLayout(
            column_count=1,
            columns=[columns.Column(0, (0.0, float(page_width)), list(order))],
            reading_order=order,
        )
    split_x, _ = split
    left = [i for i in body if columns._center_x(bboxes[i]) < split_x]
    right = [i for i in body if columns._center_x(bboxes[i]) >= split_x]
    left_sorted = columns._sort_by_y(left, bboxes)
    right_sorted = columns._sort_by_y(right, bboxes)
    body_min_y = min(bboxes[i][1] for i in body)
    body_max_y = max(bboxes[i][3] for i in body)
    top_full = columns._sort_by_y(
        [i for i in full if bboxes[i][1] < body_min_y], bboxes)
    bottom_full = columns._sort_by_y(
        [i for i in full if bboxes[i][1] >= body_min_y
         and bboxes[i][3] <= body_max_y + 1e-6], bboxes)
    band_full = columns._sort_by_y(
        [i for i in full if i not in top_full and i not in bottom_full], bboxes)
    return columns.ColumnLayout(
        column_count=2,
        columns=[columns.Column(0, (0.0, split_x), list(left_sorted)),
                 columns.Column(1, (split_x, float(page_width)), list(right_sorted))],
        reading_order=top_full + band_full + left_sorted + right_sorted + bottom_full,
    )


def _assemble(page_doc: dict[str, Any], formula_pages: dict[int, list[dict[str, Any]]],
              *, legacy: bool) -> dict[str, Any]:
    page_copy = copy.deepcopy(page_doc)
    formula_copy = copy.deepcopy(formula_pages)
    if legacy:
        with patch.object(canonical_assembly, "detect_columns", _legacy_detect_columns):
            result = assemble_document(page_copy, formula_pages=formula_copy)
    else:
        result = assemble_document(page_copy, formula_pages=formula_copy)
    apply_computed_ids(result, compute_ids(result))
    validate_document(result)
    return result


def _element_base_key(element: dict[str, Any]) -> str:
    source_spans = [span for span in element.get("source_spans") or []
                    if span.get("role") == "primary"]
    if not source_spans:
        source_spans = list(element.get("source_spans") or [])[:1]
    primary_spans = [{"physical_page": span.get("physical_page"),
                      "bbox": span.get("bbox"), "role": span.get("role")}
                     for span in source_spans]
    payload = {
        "type": element.get("type"),
        "primary_spans": primary_spans,
        "source_region_ids": element.get("source_region_ids"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _occurrence_keys(elements: list[dict[str, Any]]) -> list[tuple[str, int]]:
    seen: Counter[str] = Counter()
    result = []
    for element in elements:
        base = _element_base_key(element)
        result.append((base, seen[base]))
        seen[base] += 1
    return result


def _order_changes(before: dict[str, Any], after: dict[str, Any]) -> int:
    old = _occurrence_keys(before.get("elements") or [])
    new = _occurrence_keys(after.get("elements") or [])
    if Counter(old) != Counter(new):
        raise AssertionError("T4 before/after element multisets differ")
    new_positions = {key: index for index, key in enumerate(new)}
    return sum(index != new_positions[key] for index, key in enumerate(old))


def _page_text_layouts(page_doc: dict[str, Any]) -> dict[int, columns.ColumnLayout]:
    result = {}
    for page in page_doc.get("pages") or []:
        bboxes = [e["bbox"] for e in page.get("elements") or []
                  if columns._is_finite_box(e.get("bbox") or [])]
        result[int(page["physical_page"])] = columns.detect_columns(
            bboxes, page_width=float(page.get("width") or 0),
            page_height=float(page.get("height") or 0))
    return result


def _primary_position(element: dict[str, Any]) -> tuple[int, float] | None:
    spans = [span for span in element.get("source_spans") or []
             if span.get("role") == "primary"] or list(element.get("source_spans") or [])
    if not spans:
        return None
    span = spans[0]
    bbox = span.get("bbox") or []
    if len(bbox) != 4:
        return None
    return int(span["physical_page"]), float(bbox[1])


def _single_column_y_inversions(
    canonical: dict[str, Any], fixed_layouts: dict[int, columns.ColumnLayout]
) -> int:
    by_page: dict[int, list[float]] = defaultdict(list)
    for element in canonical.get("elements") or []:
        if element.get("type") != "text":
            continue
        pos = _primary_position(element)
        if pos is not None:
            by_page[pos[0]].append(pos[1])
    count = 0
    for page, values in by_page.items():
        # A fixed two-column page legitimately jumps from the bottom of the
        # left column to the top of the right; exclude such pages from this
        # single-column inversion signal.
        if fixed_layouts.get(page, columns.ColumnLayout(1, [], [])).column_count != 1:
            continue
        count += sum(next_y + Y_INVERSION_TOLERANCE < current_y
                     for current_y, next_y in zip(values, values[1:]))
    return count


def _union_by_page(chunk: Any) -> dict[int, list[float]]:
    grouped: dict[int, list[list[float]]] = defaultdict(list)
    for span in chunk.source_spans:
        grouped[int(span.physical_page)].append(list(span.bbox))
    return {page: [min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)]
            for page, boxes in grouped.items()}


def _intersection_metrics(a: list[float], b: list[float]) -> tuple[float, float]:
    area = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    if not area:
        return 0.0, 0.0
    other_area = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = area + other_area - intersection
    return intersection / area, intersection / union if union else 0.0


def _source_span_overlap(old_chunk: Any, new_chunk: Any) -> float:
    """Coverage of old source rectangles by one new chunk's rectangles.

    This deliberately compares individual rectangles rather than their union:
    a bad old reading order can put far-apart lines in one chunk, whose union
    covers unrelated intervening tables or paragraphs.
    """
    new_by_page: dict[int, list[list[float]]] = defaultdict(list)
    for span in new_chunk.source_spans:
        new_by_page[int(span.physical_page)].append(list(span.bbox))
    covered = 0.0
    total = 0.0
    for span in old_chunk.source_spans:
        box = list(span.bbox)
        area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
        total += area
        covered += max((max(0.0, min(box[2], candidate[2]) - max(box[0], candidate[0]))
                        * max(0.0, min(box[3], candidate[3]) - max(box[1], candidate[1]))
                        for candidate in new_by_page.get(int(span.physical_page), [])),
                       default=0.0)
    return covered / total if total else 0.0


def _chunk_body(chunk: Any) -> str:
    prefix = _format_section_path(chunk.metadata.get("section_path") or [])
    return chunk.text[len(prefix):] if prefix and chunk.text.startswith(prefix) else chunk.text


def _text(element: dict[str, Any]) -> str:
    content = element.get("content") or {}
    return str(content.get("text") or content.get("caption") or content.get("latex") or "")


def _target_trace(page_doc: dict[str, Any], before: dict[str, Any],
                  after: dict[str, Any]) -> dict[str, Any]:
    page = next(p for p in page_doc["pages"] if int(p["physical_page"]) == TARGET_PAGE)
    source = [e for e in page.get("elements") or []
              if columns._is_finite_box(e.get("bbox") or [])]
    boxes = [e["bbox"] for e in source]
    legacy = _legacy_detect_columns(
        boxes, page_width=float(page["width"]), page_height=float(page["height"]))
    fixed = columns.detect_columns(
        boxes, page_width=float(page["width"]), page_height=float(page["height"]))
    def layout_payload(layout: columns.ColumnLayout) -> dict[str, Any]:
        return {
            "column_count": layout.column_count,
            "reading_order": layout.reading_order,
            "ordered_lines": [{"source_index": index, "bbox": source[index]["bbox"],
                               "text": source[index].get("text") or ""}
                              for index in layout.reading_order],
        }
    def canonical_page(doc: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for element in doc.get("elements") or []:
            pos = _primary_position(element)
            if pos and pos[0] == TARGET_PAGE:
                result.append({"element_id": element["element_id"],
                               "bbox": (element.get("source_spans") or [{}])[0].get("bbox"),
                               "section_path": element.get("section_path") or [],
                               "text": _text(element)})
        return result
    return {"legacy_layout": layout_payload(legacy),
            "fixed_layout": layout_payload(fixed),
            "canonical_before": canonical_page(before),
            "canonical_after": canonical_page(after)}


def _write_rootcause(trace: dict[str, Any]) -> None:
    def line_of(module: Any, needle: str) -> int:
        source = Path(module.__file__).read_text(encoding="utf-8").splitlines()
        return next(index for index, line in enumerate(source, 1) if needle in line)

    assembly_line = line_of(canonical_assembly, "layout = detect_columns(")
    detector_line = line_of(columns, "centers = sorted(")
    edge_gutter_line = line_of(columns, "edge_gutter = min(")
    source_lines = trace["legacy_layout"]["ordered_lines"]
    relevant = [row for row in source_lines if 199 <= row["bbox"][1] <= 370]
    after = [row for row in trace["canonical_after"] if 199 <= row["bbox"][1] <= 370]
    lines = [
        "# T4 阅读顺序根因证据", "",
        "## 确定性根因", "",
        f"- `src/application/canonical_assembly.py:{assembly_line}` 起的 `assemble_document` 在页内收集 item 后调用 `detect_columns`，再按其 `reading_order` 形成全局元素流；section_path 随该流逐项推断，因此排序错误会直接变成归属错误。",
        f"- `src/ingestion/column_detection.py:{detector_line}` 起的旧判定只要求非满宽 bbox 的横向中心形成足够大的间隙，未验证左右组之间是否存在真实的空白列缝。p25 的缩进首行、左边距短续行、居中标题/布局框形成两个中心簇，但 bbox 横向重叠，仍被误判为 2 栏。",
        "- 旧布局随后执行左栏全部自上而下、再右栏全部自上而下，短续行被整体前移；heading 栈在错误顺序上运行，于是续行继承了先前的 `6.1.6`。",
        f"- `src/ingestion/column_detection.py:{edge_gutter_line}` 的修复增加真实边缘列缝门槛：`min(right.x0) - max(left.x1) >= 0.02 * page_width`。p25 横向重叠，不再允许 column-major 重排。",
        "", "## p25 中间态", "",
        f"- 旧列判定：`column_count={trace['legacy_layout']['column_count']}`，阅读顺序索引 `{trace['legacy_layout']['reading_order']}`。",
        f"- 新列判定：`column_count={trace['fixed_layout']['column_count']}`，阅读顺序索引 `{trace['fixed_layout']['reading_order']}`。",
        "", "旧排序中 6.1.8 范围的行：", "", "```json",
        json.dumps(relevant, ensure_ascii=False, indent=2), "```", "",
        "修复后 Canonical 6.1.8 序列：", "", "```json",
        json.dumps(after, ensure_ascii=False, indent=2), "```", "",
        "以上均为离线内存装配；未覆盖正式 Canonical、未 ingest/publish。", "",
    ]
    (ROOT / "eval/audits/ocr_fix/T4_order_rootcause.md").write_text(
        "\n".join(lines), encoding="utf-8")


def main() -> None:
    page_docs = _load_jsonl(ROOT / "data/canonical" / f"{CORPUS}-documents.jsonl")
    samples = _load_jsonl(ROOT / "eval/audits/ocr_sampling/sample_sheet.jsonl")
    judgments = {row["sample_id"]: row for row in _load_jsonl(
        ROOT / "eval/audits/ocr_sampling/judgments_claude.jsonl")}
    samples_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        samples_by_sha[sample["document_sha256"]].append(sample)

    formula_dir = ROOT / "data/model_runtime/corpus_formulas" / FORMULA_RUN
    totals: Counter[str] = Counter()
    document_rows = []
    sample_rows = []
    target_trace = None
    for page_doc in page_docs:
        sha = page_doc["sha256"]
        formula_pages, failures = load_formula_pages(
            sha, range(1, len(page_doc.get("pages") or []) + 1),
            cache_dir=str(formula_dir))
        if failures:
            raise AssertionError(f"formula cache failures for {sha}: {failures}")
        before = _assemble(page_doc, formula_pages, legacy=True)
        after = _assemble(page_doc, formula_pages, legacy=False)
        before_chunks = chunk_document(before)
        after_chunks = chunk_document(after)
        published = json.loads((ROOT / "data/canonical/v2" / CORPUS /
                                f"{sha}.canonical.json").read_text(encoding="utf-8"))
        published_chunks = {chunk.chunk_id: chunk for chunk in chunk_document(published)}
        scanned = is_scanned_document(before_chunks)
        layouts = _page_text_layouts(page_doc)
        moved = _order_changes(before, after)
        inversions_before = _single_column_y_inversions(before, layouts)
        inversions_after = _single_column_y_inversions(after, layouts)
        legacy_two = 0
        fixed_two = 0
        preserved_two = 0
        reclassified_single = 0
        for page in page_doc.get("pages") or []:
            boxes = [e["bbox"] for e in page.get("elements") or []
                     if columns._is_finite_box(e.get("bbox") or [])]
            legacy_layout = _legacy_detect_columns(
                boxes, page_width=float(page.get("width") or 0),
                page_height=float(page.get("height") or 0))
            fixed_layout = columns.detect_columns(
                boxes, page_width=float(page.get("width") or 0),
                page_height=float(page.get("height") or 0))
            legacy_two += int(legacy_layout.column_count == 2)
            fixed_two += int(fixed_layout.column_count == 2)
            preserved_two += int(legacy_layout.column_count == fixed_layout.column_count == 2
                                 and legacy_layout.reading_order == fixed_layout.reading_order)
            reclassified_single += int(legacy_layout.column_count == 2
                                       and fixed_layout.column_count == 1)
        row = {
            "document_name": page_doc.get("file_name"),
            "document_sha256": sha, "scanned": scanned,
            "elements": len(after.get("elements") or []),
            "elements_with_changed_position": moved,
            "single_column_y_inversions_before": inversions_before,
            "single_column_y_inversions_after": inversions_after,
            "legacy_two_column_pages": legacy_two,
            "fixed_two_column_pages": fixed_two,
            "preserved_two_column_pages_same_order": preserved_two,
            "legacy_two_column_pages_reclassified_single": reclassified_single,
            "chunks_before": len(before_chunks), "chunks_after": len(after_chunks),
        }
        document_rows.append(row)
        totals["documents"] += 1
        totals["scanned_documents"] += int(scanned)
        totals["elements"] += row["elements"]
        totals["elements_with_changed_position"] += moved
        totals["single_column_y_inversions_before"] += inversions_before
        totals["single_column_y_inversions_after"] += inversions_after
        totals["legacy_two_column_pages"] += legacy_two
        totals["fixed_two_column_pages"] += fixed_two
        totals["preserved_two_column_pages_same_order"] += preserved_two
        totals["legacy_two_column_pages_reclassified_single"] += reclassified_single
        prefix = "scanned" if scanned else "native"
        totals[f"{prefix}_elements_with_changed_position"] += moved
        totals[f"{prefix}_single_column_y_inversions_before"] += inversions_before
        totals[f"{prefix}_single_column_y_inversions_after"] += inversions_after
        totals["documents_with_y_inversion_increase"] += int(
            inversions_after > inversions_before)
        totals[f"{prefix}_documents_with_y_inversion_increase"] += int(
            inversions_after > inversions_before)

        for sample in samples_by_sha.get(sha, []):
            old_chunk = published_chunks.get(sample["chunk_id"])
            if old_chunk is None:
                raise AssertionError(f"published sample chunk missing: {sample['chunk_id']}")
            matches = []
            for chunk in after_chunks:
                score = _source_span_overlap(old_chunk, chunk)
                if score > 0:
                    matches.append((score, chunk))
            matches.sort(key=lambda row: (-row[0], row[1].chunk_id))
            matched = matches[0][1] if matches else None
            new_body = _chunk_body(matched) if matched else None
            judgment = judgments.get(sample["sample_id"])
            sample_rows.append({
                "sample_id": sample["sample_id"],
                "document_name": sample["document_name"],
                "physical_pages": sample["physical_pages"],
                "old_body": sample["body_text"], "new_body": new_body,
                "body_changed": new_body != sample["body_text"] if matched else None,
                "new_chunk_id": matched.chunk_id if matched else None,
                "source_span_overlap_ratio": round(matches[0][0], 6) if matches else 0.0,
                "claude_text_fidelity": judgment.get("text_fidelity") if judgment else None,
                "claude_error_types": judgment.get("error_types") if judgment else [],
                "claude_evidence": judgment.get("evidence") if judgment else None,
            })
        if sha.startswith(TARGET_SHA_PREFIX):
            target_trace = _target_trace(page_doc, before, after)

    error_rows = [row for row in sample_rows if row["claude_text_fidelity"] == "error"]
    result = {
        "schema_version": "t4-order-recompute-v1", "corpus_version": CORPUS,
        "method": {
            "before": "in-memory assemble with exact pre-T4 detector",
            "after": "in-memory assemble with edge-gutter detector",
            "changed_position": "same source/content occurrence has a different elements[] index",
            "y_inversion": ("adjacent text elements on a page classified single-column by the fixed "
                            f"detector, next_y + {Y_INVERSION_TOLERANCE} < current_y"),
            "writes_formal_artifacts": False,
        },
        "totals": dict(totals),
        "sample_matching": {
            "samples": len(sample_rows),
            "matched": sum(row["new_body"] is not None for row in sample_rows),
            "body_changed": sum(row["body_changed"] is True for row in sample_rows),
            "body_unchanged": sum(row["body_changed"] is False for row in sample_rows),
            "claude_error_samples": len(error_rows),
            "claude_error_matched": sum(row["new_body"] is not None for row in error_rows),
            "claude_error_body_changed": sum(row["body_changed"] is True for row in error_rows),
        },
        "documents": document_rows,
        "samples": sample_rows,
        "claude_error_samples": error_rows,
        "p25_trace": target_trace,
    }
    output = ROOT / "eval/audits/ocr_fix/T4_order_recompute.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if target_trace is None:
        raise AssertionError("target p25 document not found")
    _write_rootcause(target_trace)
    print(json.dumps({"totals": result["totals"],
                      "sample_matching": result["sample_matching"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
