"""V2 Canonical Assembly (plan 1.5).

Assembles a V2 CanonicalDocument dict from V1 page-owned artifacts:
``data/canonical/corpus-*-documents.jsonl`` (per-document) plus the optional
per-page formula cache ``data/model_runtime/corpus_formulas/<config>/<sha>-<page>.json``
and a registry metadata override.

The assembly is a pure re-organisation layer: it never invokes OCR / PDF engines.
It sequences V1 page elements into a single logical ``elements[]`` stream using
:mod:`src.ingestion.column_detection` (within-page reading order) and
:mod:`src.ingestion.heading_hierarchy` (section_path), then wraps each item in the
unified V2 Element envelope with a typed ``content`` union, ``source_spans``,
``provenance`` and ``links``.

The returned dict is structurally valid but carries placeholder IDs
(``canonical_id`` / ``canonical_content_id`` / ``metadata_fingerprint``); the
caller fills them via :func:`src.application.canonical_validation.compute_ids`
and verifies via :func:`src.application.canonical_validation.validate_document`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.ingestion.column_detection import detect_columns
from src.ingestion.heading_hierarchy import (
    HeadingInput,
    HeadingNode,
    HIGH_CONFIDENCE,
    build_section_path,
    infer_headings,
)

__all__ = ["assemble_document", "load_formula_pages", "CECS758_SHA"]

CECS758_SHA = "f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941"

# Tuning thresholds for formula region filtering.
_FORMULA_MIN_SCORE = 0.6
_FORMULA_MIN_AREA = 500.0  # page-points^2; excludes tiny inline symbol fragments

# Page-points window below a formula within which "式中" context is gathered.
_CONTEXT_MAX_GAP = 180.0

# Regex helpers
_BARE_NUM_RE = re.compile(r"^(\d+)(?:\s|$)")
_DOTTED_NUM_RE = re.compile(r"^(\d+(?:\.\d+)+)")
_PAREN_NUM_RE = re.compile(r"\(([0-9][0-9.\-]*)\)")
_PAGE_NUM_RE = re.compile(r"^[•·]?\s*\d+\s*[•··]?$")
_CHINESE_RE = re.compile(r"[一-鿿]")
_TABLE_CAPTION_RE = re.compile(r"^(续)?表\s*\d")
_FIGURE_CAPTION_RE = re.compile(r"^图\s*\d")
_LIST_NUM_RE = re.compile(r"^(\d+)\s*[\n]")

_CJK = "[一-鿿]"


# --------------------------------------------------------------------------- #
# Internal data shapes
# --------------------------------------------------------------------------- #


@dataclass
class _PageItem:
    """A single within-page item that will become (part of) a V2 Element."""

    kind: str  # "text" | "formula" | "table" | "figure"
    page: int
    bbox: list[float]
    text: str = ""
    ref: dict[str, Any] | None = None
    # Fields populated during assembly
    source_index: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def assemble_document(
    v1_document: dict[str, Any],
    *,
    formula_pages: dict[int, list[dict[str, Any]]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a V2 CanonicalDocument dict from a V1 document.

    Args:
        v1_document: a single V1 document dict (one ``corpus-*-documents.jsonl``
            row). Only ``sha256`` / ``file_name`` / ``pages`` / ``metadata`` /
            ``processing_run_id`` / ``config_hash`` are consumed.
        formula_pages: optional mapping ``physical_page -> list[formula region]``
            (the ``result.pages[0].regions`` of the per-page formula cache). When
            ``None`` the document has no FormulaElements.
        metadata: optional registry metadata override; merged on top of the V1
            document-level ``metadata``.

    Returns:
        A V2 CanonicalDocument dict with placeholder canonical IDs. The caller
        must run :func:`compute_ids` then :func:`validate_document` before
        emitting the artifact.
    """
    formula_pages = formula_pages or {}
    v1_pages = v1_document.get("pages") or []
    sha = (v1_document.get("sha256") or "").lower()
    page_count = len(v1_pages)

    # 1. Build per-page ordered item lists + global reading-order slots. -------
    global_slots: list[_PageItem] = []
    pages_meta: list[dict[str, Any]] = []
    page_by_number: dict[int, dict[str, Any]] = {}
    for idx, v1_page in enumerate(v1_pages):
        pno = int(v1_page.get("physical_page") or (idx + 1))
        page_by_number[pno] = v1_page
        width = float(v1_page.get("width") or 0.0)
        height = float(v1_page.get("height") or 0.0)
        rotation = int(v1_page.get("rotation") or 0) % 360
        if rotation not in (0, 90, 180, 270):
            rotation = 0
        parse_status, parse_error = _parse_status(v1_page)
        pages_meta.append({
            "physical_page": pno,
            "printed_label": v1_page.get("display_page_label"),
            "width": width,
            "height": height,
            "unit": "pt",
            "rotation_degrees": rotation,
            "parse_status": parse_status,
            "parse_error": parse_error,
        })
        if parse_status != "parsed":
            continue  # blank/failed pages contribute no slots
        items = _collect_page_items(v1_page, pno, width, height, formula_pages.get(pno))
        layout = detect_columns(
            [it.bbox for it in items], page_width=width, page_height=height
        )
        for order_idx in layout.reading_order:
            global_slots.append(items[order_idx])

    # Assign global source_index (= position in logical reading order).
    for i, slot in enumerate(global_slots):
        slot.source_index = i

    # 2. Heading detection over the global text stream. -----------------------
    heading_inputs: list[HeadingInput] = []
    for slot in global_slots:
        if slot.kind != "text":
            continue
        candidate = _heading_candidate_text(slot.text)
        if candidate is not None:
            heading_inputs.append(
                HeadingInput(
                    text=candidate,
                    bbox=tuple(slot.bbox),
                    source_index=slot.source_index,
                )
            )
    headings = infer_headings(heading_inputs)
    heading_by_index = {h.source_index: h for h in headings}

    # 3. Build V2 elements. ----------------------------------------------------
    span_counter = _Counter("s")
    elem_counter = _Counter("e")
    elements: list[dict[str, Any]] = []
    # Track tables by (page, number) for cross-page `continues` links.
    table_registry: dict[tuple[int, str], str] = {}

    # First pass: identify consumed text elements (formula/table/figure/caption)
    # so they are not also emitted as TextElements.
    consumed_indices: set[int] = set()
    for slot in global_slots:
        if slot.kind == "text":
            continue
        # Mark overlapping text elements as consumed.
        for t_slot in global_slots:
            if t_slot.kind != "text" or t_slot.page != slot.page:
                continue
            if _bbox_overlap(slot.bbox, t_slot.bbox):
                consumed_indices.add(t_slot.source_index)
    # Table/figure caption text elements are consumed by their table/figure.
    for slot in global_slots:
        if slot.kind != "text":
            continue
        s = (slot.text or "").strip()
        if _TABLE_CAPTION_RE.match(s) or _FIGURE_CAPTION_RE.match(s):
            # Only consume if a table/figure region exists on the page.
            if _has_region_kind(global_slots, slot.page, ("table", "figure")):
                consumed_indices.add(slot.source_index)

    for slot in global_slots:
        if slot.kind == "text":
            if slot.source_index in consumed_indices:
                continue
            el = _build_text_element(slot, heading_by_index, headings, span_counter, elem_counter)
            if el is not None:
                elements.append(el)
        elif slot.kind == "formula":
            el = _build_formula_element(
                slot, global_slots, heading_by_index, headings, span_counter, elem_counter
            )
            if el is not None:
                elements.append(el)
        elif slot.kind == "table":
            el = _build_table_element(
                slot, global_slots, heading_by_index, headings,
                span_counter, elem_counter, table_registry,
            )
            if el is not None:
                elements.append(el)
        elif slot.kind == "figure":
            el = _build_figure_element(
                slot, global_slots, heading_by_index, headings, span_counter, elem_counter
            )
            if el is not None:
                elements.append(el)

    # 4. Source / metadata / generation. --------------------------------------
    source = _build_source(v1_document, page_count)
    doc_metadata = _build_metadata(v1_document, metadata)
    generation = _build_generation(v1_document, formula_pages)

    return {
        "schema_version": "v2.canonical/1.0",
        "canonical_id": "canonical-sha256:PLACEHOLDER",
        "canonical_content_id": "canonical-content-sha256:PLACEHOLDER",
        "metadata_fingerprint": "metadata-sha256:PLACEHOLDER",
        "document_id": f"pdf-sha256:{sha}",
        "source": source,
        "metadata": doc_metadata,
        "generation": generation,
        "pages": pages_meta,
        "elements": elements,
    }


def load_formula_pages(
    sha: str, pages: Iterable[int], *, cache_dir: str
) -> dict[int, list[dict[str, Any]]]:
    """Load per-page formula regions from the V1 formula cache.

    Reads ``<cache_dir>/<sha>-<page>.json`` for each requested page; missing
    files are silently skipped (the page simply has no formulas). Returns a
    mapping ``physical_page -> list[region dict]`` (the
    ``result.pages[0].regions`` entries).
    """
    import json
    import os

    out: dict[int, list[dict[str, Any]]] = {}
    for pno in pages:
        path = os.path.join(cache_dir, f"{sha}-{pno}.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        result = raw.get("result") or {}
        for page_entry in result.get("pages") or []:
            regions = page_entry.get("regions") or []
            out.setdefault(int(pno), []).extend(regions)
            break  # one page per file
    return out


# --------------------------------------------------------------------------- #
# Page-item collection
# --------------------------------------------------------------------------- #


def _collect_page_items(
    v1_page: dict[str, Any],
    pno: int,
    width: float,
    height: float,
    formula_regions: list[dict[str, Any]] | None,
) -> list[_PageItem]:
    """Gather text / formula / table / figure items for a single page."""
    items: list[_PageItem] = []

    for elem in v1_page.get("elements") or []:
        bbox = _coerce_bbox(elem.get("bbox"))
        if bbox is None:
            continue
        text = (elem.get("text") or "")
        items.append(_PageItem(kind="text", page=pno, bbox=bbox, text=text, ref=elem))

    # Formula regions from the per-page formula cache.
    for region in formula_regions or []:
        if region.get("kind") != "formula":
            continue
        score = region.get("score")
        try:
            score_f = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_f = 0.0
        bbox = _coerce_bbox(region.get("bbox"))
        if bbox is None:
            continue
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if score_f < _FORMULA_MIN_SCORE or area < _FORMULA_MIN_AREA:
            continue  # drop low-confidence inline symbol fragments
        latex = region.get("raw_latex") or ""
        items.append(
            _PageItem(
                kind="formula",
                page=pno,
                bbox=bbox,
                text=latex,
                ref=region,
                extra={"score": score_f, "latex": latex},
            )
        )

    # Table / figure regions from the V1 page `regions` list (page-points bbox).
    for region in v1_page.get("regions") or []:
        kind = region.get("kind")
        if kind not in ("table", "image"):
            continue
        bbox = _coerce_bbox(region.get("bbox"))
        if bbox is None:
            continue
        items.append(
            _PageItem(
                kind="figure" if kind == "image" else "table",
                page=pno,
                bbox=bbox,
                ref=region,
                extra={"reading_order": region.get("reading_order")},
            )
        )

    return items


# --------------------------------------------------------------------------- #
# Text element
# --------------------------------------------------------------------------- #


def _build_text_element(
    slot: _PageItem,
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
) -> dict[str, Any] | None:
    text = _normalise_text(slot.text)
    if not text:
        return None
    role = _infer_text_role(slot, heading_by_index.get(slot.source_index))
    label = _text_label(slot, heading_by_index.get(slot.source_index))
    section_path = build_section_path(headings, slot.source_index)
    primary_span = _make_span(span_counter, "primary", slot.page, slot.bbox)
    provenance = [_provenance("extract", "native_text", "native-1", None)]
    return {
        "element_id": elem_counter.next(),
        "type": "text",
        "role": role,
        "label": label,
        "section_path": section_path,
        "content": {"text": text, "list": _list_info(slot.text)},
        "source_spans": [primary_span],
        "provenance": provenance,
        "links": [],
    }


def _infer_text_role(slot: _PageItem, heading: HeadingNode | None) -> str:
    raw = (slot.text or "").strip()
    if _PAGE_NUM_RE.match(raw):
        return "page_number"
    if heading is not None:
        # Short title -> heading; long body -> clause (条文 that anchors a path).
        title = heading.title or ""
        if len(title) <= 15:
            return "heading"
        return "clause"
    # Bare number + long Chinese body -> list item.
    m = _BARE_NUM_RE.match(raw)
    if m:
        rest = raw[m.end():].strip()
        if rest and _CHINESE_RE.search(rest) and len(rest) > 15:
            return "list_item"
        if rest and _CHINESE_RE.search(rest):
            return "paragraph"
    return "paragraph"


def _text_label(slot: _PageItem, heading: HeadingNode | None) -> str | None:
    if heading is not None and heading.label:
        return heading.label
    return None


def _list_info(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    m = _BARE_NUM_RE.match(raw)
    if not m:
        return None
    rest = raw[m.end():].strip()
    if rest and _CHINESE_RE.search(rest) and len(rest) > 15:
        # Looks like a numbered list item.
        return {"marker": m.group(1), "level": 0}
    return None


# --------------------------------------------------------------------------- #
# Formula element
# --------------------------------------------------------------------------- #


def _build_formula_element(
    slot: _PageItem,
    global_slots: list[_PageItem],
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
) -> dict[str, Any] | None:
    latex = (slot.extra.get("latex") or "").strip()
    if not latex:
        return None
    formula_number = _extract_formula_number(slot, global_slots)
    context_text, context_spans = _gather_formula_context(slot, global_slots, span_counter)
    primary_span = _make_span(span_counter, "primary", slot.page, slot.bbox)
    section_path = build_section_path(headings, slot.source_index)
    score = slot.extra.get("score")
    provenance = [
        _provenance(
            "recognize",
            "formula_recognition",
            "formula-1",
            round(float(score), 4) if score is not None else None,
        )
    ]
    return {
        "element_id": elem_counter.next(),
        "type": "formula",
        "role": "display_formula",
        "label": formula_number,
        "section_path": section_path,
        "content": {
            "formula_number": formula_number,
            "latex": latex,
            "recognized_formula": None,
            "context_text": context_text,
        },
        "source_spans": [primary_span] + context_spans,
        "provenance": provenance,
        "links": [],
    }


def _extract_formula_number(slot: _PageItem, global_slots: list[_PageItem]) -> str | None:
    """Find a ``(N)`` style number in the native text overlapping the formula."""
    for t in global_slots:
        if t.kind != "text" or t.page != slot.page:
            continue
        if not _bbox_overlap(slot.bbox, t.bbox):
            continue
        m = _PAREN_NUM_RE.search(t.text or "")
        if m:
            return m.group(1)
    return None


def _gather_formula_context(
    slot: _PageItem,
    global_slots: list[_PageItem],
    span_counter: _Counter,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Collect "式中" / variable-definition text below a formula.

    Walks forward in reading order over same-page text elements whose bbox sits
    below the formula, stopping at the next clause / heading / list / caption or
    another formula / table. Returns concatenated context text + context spans.
    """
    pieces: list[str] = []
    spans: list[dict[str, Any]] = []
    fx1 = slot.bbox[3]
    for t in global_slots:
        if t.source_index <= slot.source_index:
            continue
        if t.page != slot.page:
            continue
        if t.bbox[1] < fx1 - 1.0:  # not strictly below the formula
            continue
        if t.bbox[1] - fx1 > _CONTEXT_MAX_GAP:
            break
        if t.kind != "text":
            break  # formula/table/figure ends the context block
        s = (t.text or "").strip()
        if not s:
            continue
        if _is_context_stop(s):
            break
        pieces.append(_normalise_text(s))
        spans.append(_make_span(span_counter, "context", t.page, t.bbox))
    context_text = "\n".join(pieces) if pieces else None
    return context_text, spans


def _is_context_stop(text: str) -> bool:
    """True if a text line is a clause/heading/list/caption that ends context."""
    s = text.strip()
    if not s:
        return False
    if _TABLE_CAPTION_RE.match(s) or _FIGURE_CAPTION_RE.match(s):
        return True
    if _DOTTED_NUM_RE.match(s):
        return True
    # Bare number followed by Chinese -> list item / heading.
    m = _BARE_NUM_RE.match(s)
    if m and _CHINESE_RE.search(s[m.end():]):
        return True
    return False


# --------------------------------------------------------------------------- #
# Table element
# --------------------------------------------------------------------------- #


def _build_table_element(
    slot: _PageItem,
    global_slots: list[_PageItem],
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
    table_registry: dict[tuple[int, str], str],
) -> dict[str, Any] | None:
    pno = slot.page
    region_bbox = slot.bbox
    # Gather text elements inside the table region (by overlap).
    in_table: list[_PageItem] = []
    caption: str | None = None
    table_number: str | None = None
    cx_lo, cy_lo, cx_hi, cy_hi = region_bbox
    for t in global_slots:
        if t.kind != "text" or t.page != pno:
            continue
        s = (t.text or "").strip()
        cap_m = _TABLE_CAPTION_RE.match(s)
        if cap_m:
            # Caption may sit just above the table region (no overlap) or on it.
            near = (
                _bbox_overlap(region_bbox, t.bbox)
                or (cy_lo - 40.0 <= t.bbox[3] <= cy_lo + 12.0)
            )
            if near and not caption:
                table_number = cap_m.group(0).replace("续", "").strip()
                caption = s
                continue
        if _bbox_overlap(region_bbox, t.bbox):
            in_table.append(t)

    cells, row_count, column_count = _build_table_cells(in_table, region_bbox)
    if row_count == 0:
        # Ensure at least a single-cell stub so the grid is positive.
        row_count, column_count = 1, 1
        cells = [{"row": 0, "column": 0, "row_span": 1, "column_span": 1,
                  "role": "unknown", "text": "", "source_span_ids": []}]

    primary_span = _make_span(span_counter, "primary", pno, region_bbox)
    section_path = build_section_path(headings, slot.source_index)
    element_id = elem_counter.next()

    # Cross-page `continues` link.
    links: list[dict[str, Any]] = []
    is_continuation = bool(caption and caption.startswith("续表"))
    if table_number:
        key = (pno, table_number)
        if is_continuation:
            # Find the most recent prior table with the same number.
            for (other_page, other_num), other_id in list(table_registry.items()):
                if other_num == table_number and other_page < pno:
                    links.append({"type": "continues", "target_element_id": other_id})
                    break
        else:
            table_registry[key] = element_id

    provenance = [_provenance("detect", "layout_detection", "layout-1", None)]
    label = table_number
    return {
        "element_id": element_id,
        "type": "table",
        "role": "table",
        "label": label,
        "section_path": section_path,
        "content": {
            "row_count": row_count,
            "column_count": column_count,
            "caption": caption,
            "cells": cells,
            "notes": [],
        },
        "source_spans": [primary_span],
        "provenance": provenance,
        "links": links,
    }


def _build_table_cells(
    in_table: list[_PageItem], region_bbox: list[float]
) -> tuple[list[dict[str, Any]], int, int]:
    """Heuristically reconstruct a cell grid from text elements inside a table.

    V1 has no structured table cells (``tables: []``) for CECS758; the table
    content lives in the native text elements. Elements are ordered by (y, x).
    Left-side elements (x0 < region centre) hold the ``指标参数 | 参照值`` columns
    as stacked lines; right-side elements hold the ``来源`` column. Lines become
    cells; row index advances per left-element.
    """
    if not in_table:
        return [], 0, 0
    mid_x = (region_bbox[0] + region_bbox[2]) / 2.0
    ordered = sorted(in_table, key=lambda it: (it.bbox[1], it.bbox[0]))

    rows: list[list[str | None]] = []
    current: list[str | None] = [None, None, None]
    has_left = False
    column_count = 2

    for elem in ordered:
        lines = [ln.strip() for ln in (elem.text or "").split("\n") if ln.strip()]
        if not lines:
            continue
        is_left = elem.bbox[0] < mid_x
        if is_left:
            if has_left:
                rows.append(current)
                current = [None, None, None]
                has_left = False
            for i, line in enumerate(lines[:3]):
                current[i] = (current[i] + "\n" + line) if current[i] else line
            has_left = True
            if len(lines) >= 3:
                column_count = max(column_count, 3)
        else:
            # Right column (来源) attaches to the current row.
            merged = " ".join(lines)
            current[2] = (current[2] + " " + merged) if current[2] else merged
            column_count = max(column_count, 3)

    if has_left:
        rows.append(current)

    cells: list[dict[str, Any]] = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text is None or text == "":
                continue
            role = "header" if r == 0 else ("stub" if c == 0 else "data")
            cells.append({
                "row": r,
                "column": c,
                "row_span": 1,
                "column_span": 1,
                "role": role,
                "text": text,
                "source_span_ids": [],
            })
    # Guard: ensure at least one cell.
    if not cells:
        cells = [{"row": 0, "column": 0, "row_span": 1, "column_span": 1,
                  "role": "unknown", "text": "", "source_span_ids": []}]
        return cells, 1, 1
    row_count = max(c["row"] for c in cells) + 1
    column_count = max(column_count, max(c["column"] for c in cells) + 1)
    return cells, row_count, column_count


# --------------------------------------------------------------------------- #
# Figure element
# --------------------------------------------------------------------------- #


def _build_figure_element(
    slot: _PageItem,
    global_slots: list[_PageItem],
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
) -> dict[str, Any]:
    pno = slot.page
    bbox = slot.bbox
    caption, caption_slot = _find_figure_caption(slot, global_slots)
    references, ref_spans = _find_figure_references(slot, global_slots, span_counter)
    primary_span = _make_span(span_counter, "primary", pno, bbox)
    section_path = build_section_path(headings, slot.source_index)
    asset = _placeholder_asset(pno, bbox)
    provenance = [_provenance("crop", "image_extraction", "image-1", None)]
    return {
        "element_id": elem_counter.next(),
        "type": "figure",
        "role": "figure",
        "label": _figure_label(caption),
        "section_path": section_path,
        "content": {
            "caption": caption,
            "references": references,
            "asset": asset,
        },
        "source_spans": [primary_span] + ref_spans,
        "provenance": provenance,
        "links": [],
    }


def _find_figure_caption(
    slot: _PageItem, global_slots: list[_PageItem]
) -> tuple[str | None, _PageItem | None]:
    for t in global_slots:
        if t.kind != "text" or t.page != slot.page:
            continue
        s = (t.text or "").strip()
        if _FIGURE_CAPTION_RE.match(s):
            # Must be near the figure (below or overlapping).
            if t.bbox[1] >= slot.bbox[3] - 30 or _bbox_overlap(slot.bbox, t.bbox):
                return s, t
    return None, None


def _find_figure_references(
    slot: _PageItem,
    global_slots: list[_PageItem],
    span_counter: _Counter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for t in global_slots:
        if t.kind != "text" or t.page != slot.page:
            continue
        s = (t.text or "")
        # Reference: text mentions "（图N）" and is near the figure.
        if "（图" in s or "(图" in s:
            span = _make_span(span_counter, "context", t.page, t.bbox)
            spans.append(span)
            refs.append({"text": s.strip(), "source_span_ids": [span["span_id"]]})
    return refs, spans


def _figure_label(caption: str | None) -> str | None:
    if not caption:
        return None
    m = re.match(r"(图\s*\d+)", caption)
    return m.group(1).replace(" ", "") if m else None


def _placeholder_asset(page: int, bbox: list[float]) -> dict[str, Any]:
    x0, y0, x1, y1 = bbox
    ref = f"assets/sha256/placeholder-p{page}-{x0:.0f}-{y0:.0f}-{x1:.0f}-{y1:.0f}.png"
    sha = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    # pt -> px @ 96 dpi (pt * 96/72 = pt * 4/3).
    pw = max(1, int(round((x1 - x0) * 96.0 / 72.0)))
    ph = max(1, int(round((y1 - y0) * 96.0 / 72.0)))
    return {
        "ref": ref,
        "sha256": sha,
        "media_type": "image/png",
        "pixel_width": pw,
        "pixel_height": ph,
    }


# --------------------------------------------------------------------------- #
# Source / metadata / generation
# --------------------------------------------------------------------------- #


def _build_source(v1_document: dict[str, Any], page_count: int) -> dict[str, Any]:
    sha = (v1_document.get("sha256") or "").lower()
    file_name = v1_document.get("file_name") or ""
    source_uri = v1_document.get("source_uri") or None
    if not source_uri:
        source_uri = None
    return {
        "sha256": sha,
        "file_name": file_name,
        "media_type": "application/pdf",
        "page_count": page_count,
        "source_uri": source_uri,
    }


def _build_metadata(
    v1_document: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    v1_meta = v1_document.get("metadata") or {}
    merged = dict(v1_meta)
    if override:
        merged.update(override)

    std_no = (merged.get("standard_number") or "").strip() or None
    file_name = v1_document.get("file_name") or "unknown.pdf"
    title = std_no or file_name
    if std_no:
        title = f"{std_no} {file_name}".rstrip(".pdf")

    jurisdictions = []
    jur = (merged.get("jurisdiction") or "").strip()
    if jur and jur != "未知":
        jurisdictions.append(jur)
    authorities = []
    auth = (merged.get("source_authority") or "").strip()
    if auth:
        authorities.append(auth)

    identifiers = []
    if std_no:
        identifiers.append({"scheme": "standard_number", "value": std_no})

    effective_status = (merged.get("effective_status") or "unknown").strip()
    if effective_status not in {
        "current", "not_yet_effective", "expired", "repealed", "superseded", "unknown"
    }:
        effective_status = "unknown"

    publication_date = _iso_date(merged.get("publication_date"))
    effective_from = _iso_date(merged.get("effective_from"))
    effective_to = _iso_date(merged.get("effective_to"))

    # registry snapshot id from the V1 source-evidence digest when available.
    snap = merged.get("source_evidence_sha256") or ""
    if snap:
        registry_snapshot_id = f"source-registry-sha256:{snap}"
    else:
        registry_snapshot_id = "source-registry-sha256:unknown"

    return {
        "title": title,
        "alternate_titles": [],
        "language": "zh-CN",
        "identifiers": identifiers,
        "document_kind": (merged.get("document_kind") or "unknown").strip(),
        "jurisdictions": jurisdictions,
        "issuing_authorities": authorities,
        "publication_date": publication_date,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "effective_status": effective_status,
        "effective_status_as_of": None,  # status is unknown -> no as_of
        "registry_snapshot_id": registry_snapshot_id,
    }


def _build_generation(
    v1_document: dict[str, Any], formula_pages: dict[int, list[dict[str, Any]]]
) -> dict[str, Any]:
    config_hash = (v1_document.get("config_hash") or "").lower()
    if not config_hash or len(config_hash) != 64:
        # Fall back to a deterministic hash of the file name if config_hash missing.
        config_hash = hashlib.sha256(
            (v1_document.get("file_name") or "unknown").encode("utf-8")
        ).hexdigest()
    run_id = v1_document.get("processing_run_id") or "v2-assembly-run"
    engines = [
        {"engine_id": "native-1", "name": "pymupdf", "version": "1.24"},
        {"engine_id": "layout-1", "name": "PP-DocLayout_plus-L", "version": "paddleocr-3.7.0"},
        {"engine_id": "formula-1", "name": "PP-FormulaNet_plus-M", "version": "paddleocr-3.7.0"},
        {"engine_id": "image-1", "name": "image-crop", "version": "1.0"},
    ]
    return {
        "run_id": run_id,
        "created_at": "2026-09-21T00:00:00Z",
        "pipeline": "v2-pdf-pipeline",
        "pipeline_version": "1.0",
        "config_sha256": config_hash,
        "engines": engines,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _Counter:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:06d}"


def _coerce_bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        box = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    x0, y0, x1, y1 = box
    if not (x0 <= x1 and y0 <= y1):
        return None
    if not all(0 <= v for v in box):
        # Allow slightly negative; will be clamped by validator only if > page.
        return None
    return box


def _bbox_overlap(a: list[float], b: list[float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def _make_span(
    counter: _Counter, role: str, page: int, bbox: list[float]
) -> dict[str, Any]:
    # NOTE: bbox is stored at full float precision, NOT rounded. V1 region
    # bboxes legitimately sit exactly on the page edge (e.g. x1 == page width
    # 595.365478515625); ``round(v, 4)`` would push such an edge value up to
    # 595.3655 and trip invariant #8 (``x1 <= page.width``) by ~2e-5pt. The
    # raw float is deterministic (JSON parse + IEEE-754 shortest-repr) so the
    # canonical IDs stay stable across runs.
    return {
        "span_id": counter.next(),
        "role": role,
        "physical_page": page,
        "bbox": [float(v) for v in bbox],
        "orientation_degrees": 0,
    }


def _provenance(
    operation: str, method: str, engine_id: str, confidence: float | None
) -> dict[str, Any]:
    return {
        "operation": operation,
        "method": method,
        "engine_id": engine_id,
        "confidence": confidence,
    }


def _parse_status(v1_page: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    route = (v1_page.get("extraction_route") or "").strip()
    status = (v1_page.get("decision_status") or "").strip()
    if route == "failed" or status == "failed":
        return "failed", {"code": "extraction_failed", "message": "V1 extraction route failed"}
    text = (v1_page.get("text") or "").strip()
    has_elements = bool(v1_page.get("elements"))
    if not text and not has_elements:
        return "blank", None
    return "parsed", None


def _normalise_text(text: str) -> str:
    """Unicode NFC + collapse stray whitespace; keep newlines meaningful."""
    import unicodedata

    s = unicodedata.normalize("NFC", text or "")
    # Collapse runs of spaces/tabs (keep single newlines).
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def _heading_candidate_text(text: str) -> str | None:
    """Return a normalised heading-candidate string, or None if not a candidate.

    Keeps the heading hierarchy detector honest by pre-filtering list items and
    OCR noise. Dotted numbers (X.Y...) always pass; bare numbers pass only with
    a short Chinese title; ``第X章/节`` always pass.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if _TABLE_CAPTION_RE.match(raw) or _FIGURE_CAPTION_RE.match(raw):
        return None
    # Collapse spaces around dots/digits so "5. 4. 2" -> "5.4.2".
    norm = re.sub(r"(\d)\s*\.\s*", r"\1.", raw)
    norm = re.sub(r"\.\s*(\d)", r".\1", norm)
    if re.match(r"第[一二三四五六七八九十百千零〇两\d]+[章节]", norm):
        return norm
    dotted = _DOTTED_NUM_RE.match(norm)
    if dotted:
        return norm
    bare = _BARE_NUM_RE.match(norm)
    if bare:
        rest = norm[bare.end():].strip()
        if (
            rest
            and 2 <= len(rest) <= 15
            and _CHINESE_RE.search(rest)
            and not _PAGE_NUM_RE.match(raw)
        ):
            return norm
    return None


def _has_region_kind(
    slots: list[_PageItem], page: int, kinds: tuple[str, ...]
) -> bool:
    return any(s.page == page and s.kind in kinds for s in slots)


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", s)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
