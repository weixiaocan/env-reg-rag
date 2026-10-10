"""V2 Canonical Assembly (plan 1.5).

Assembles a V2 CanonicalDocument dict from a page-intermediate OCR cache row:
``data/canonical/corpus-*-documents.jsonl`` (one dict per document, produced by
the PDF processing pipeline) plus the optional per-page formula cache
``data/model_runtime/corpus_formulas/<config>/<sha>-<page>.json`` and a registry
metadata override.

The assembly is a pure re-organisation layer: it never invokes OCR / PDF engines.
It sequences the page-intermediate elements into a single logical ``elements[]``
stream using :mod:`src.ingestion.column_detection` (within-page reading order)
and :mod:`src.ingestion.heading_hierarchy` (section_path), then wraps each item
in the unified V2 Element envelope with a typed ``content`` union,
``source_spans``, ``provenance`` and ``links``.

The page-intermediate jsonl is a read-only OCR-output cache, reused once so the
expensive OCR / layout / formula recognition runs are not repeated. Future PDFs
will produce the same page-intermediate shape directly via
:mod:`src.ingestion` + :mod:`src.application.unified_pdf`, without any legacy
intermediate -- the ``page_document`` dict shape accepted here is the stable seam
between PDF processing and V2 Canonical.

The returned dict is structurally valid but carries placeholder IDs
(``canonical_id`` / ``canonical_content_id`` / ``metadata_fingerprint``); the
caller fills them via :func:`src.application.canonical_validation.compute_ids`
and verifies via :func:`src.application.canonical_validation.validate_document`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from src.ingestion.column_detection import detect_columns
from src.ingestion.heading_hierarchy import (
    HeadingInput,
    HeadingNode,
    HIGH_CONFIDENCE,
    build_section_path,
    detect_toc_pages,
    infer_headings,
)
from src.ingestion.independent_ocr_fusion import fuse_document_pages
from src.ingestion.text_quality import is_answerable_text, score_text

__all__ = ["assemble_document", "assemble_document_v3", "load_formula_pages", "CECS758_SHA"]

CECS758_SHA = "f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941"

# Tuning thresholds for formula region filtering.
_FORMULA_MIN_SCORE = 0.6
_FORMULA_MIN_AREA = 500.0  # page-points^2; excludes tiny inline symbol fragments

# Page-points window below a formula within which "式中" context is gathered.
_CONTEXT_MAX_GAP = 180.0

# Header / footer recurring-text detection. A short text that recurs in the
# same top/bottom page band across >= _RECURRING_MIN_PAGES pages is almost
# certainly a running header / footer (standard name, chapter title, page
# footer line), not body content. Such elements are marked role="header"/
# "footer" so the V2 chunker drops them from the retrieval projection
# (``_IGNORED_ROLES``). Page numbers are caught earlier by ``_PAGE_NUM_RE``.
_HEADER_BAND_RATIO = 0.08   # top 8% of page height
_FOOTER_BAND_RATIO = 0.08   # bottom 8% of page height
_RECURRING_MIN_PAGES = 5    # same short text in >=5 pages of one band
_HEADER_FOOTER_MAX_LEN = 60  # only short texts (<=60 chars post-strip)

# Regex helpers
_BARE_NUM_RE = re.compile(r"^(\d+)(?:\s|$)")
_DOTTED_NUM_RE = re.compile(r"^(\d+(?:\.\d+)+)")
_PAREN_NUM_RE = re.compile(r"\(([0-9][0-9.\-]*)\)")
_PAGE_NUM_RE = re.compile(r"^[•·]?\s*\d+\s*[•··]?$")
_CHINESE_RE = re.compile(r"[一-鿿]")
_TABLE_CAPTION_RE = re.compile(r"^(续)?表\s*\d")
_FIGURE_CAPTION_RE = re.compile(r"^图\s*\d")
_LIST_NUM_RE = re.compile(r"^(\d+)\s*[\n]")
_OCR_PARAGRAPH_RULE_ENGINE_ID = "ocr-paragraph-reconstruction-1"
_OCR_PARAGRAPH_RULE_VERSION = "t6c-1"
_SECTION_PREFIX_RULE_ENGINE_ID = "section-prefix-rendering-1"
_SECTION_PREFIX_RULE_VERSION = "t6c-1"
_OCR_SENTENCE_END_RE = re.compile(r"[。！？；;：:]\s*$")
_OCR_DOTTED_START_RE = re.compile(r"^\d+(?:\.\d+)+\s*[一-鿿]")
_OCR_LIST_START_RE = re.compile(
    r"^(?:\d+\s*[一-鿿]|[（(]\d+[）)]\s*|\d+[）)]\s*)"
)

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
    # T02 page-level provenance carried from the page-intermediate row so each
    # element's provenance/quality can be derived from its real extraction route
    # and decision rather than fabricated as native_text/passed. ``None`` means
    # the field was missing/unknown upstream -- it must NOT be promoted to
    # native/approved (only an explicit native+approved page qualifies for
    # ``passed``).
    extraction_route: str | None = None
    decision_status: str | None = None
    decision_reasons: list[str] = field(default_factory=list)
    # Page-level OCR mean confidence (None for native; per-element confidence is
    # not persisted upstream, so the page mean is the honest best granularity).
    ocr_confidence: float | None = None
    engine_name: str = ""
    engine_version: str = ""
    # Resolved engine_id for this page (native-1 / ocr-N / unknown-1 / None for
    # failed); resolves into generation.engines. Carried so element provenance
    # uses the real per-page engine, not a single representative for the doc.
    engine_id: str | None = "native-1"
    # T02 P1a/P1b: ALL matched upstream text-region provenance (from
    # page-intermediate ``regions[]`` kind=text). An element may overlap several
    # regions; every participating region's id / quality / issues / config is
    # preserved losslessly (R6 one-to-many), and quality is the strictest over
    # all of them (order-independent).
    source_region_ids: list[str] = field(default_factory=list)
    region_qualities: list[str] = field(default_factory=list)
    region_issues: list[str] = field(default_factory=list)
    region_processing_config_ids: list[str] = field(default_factory=list)
    has_text_regions: bool = False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def assemble_document_v3(
    page_document: dict[str, Any],
    *,
    formula_pages: dict[int, list[dict[str, Any]]] | None = None,
    metadata: dict[str, Any] | None = None,
    independent_ocr_run_dir: str | None = None,
    raw_independent_ocr_run_dir: str | None = None,
) -> dict[str, Any]:
    """Assemble through the region-first v3 middle layer."""
    from src.ingestion.block_assembly import prepare_page_document_v3

    prepared = prepare_page_document_v3(
        page_document, independent_ocr_run_dir=independent_ocr_run_dir,
        raw_independent_ocr_run_dir=raw_independent_ocr_run_dir)
    return assemble_document(
        prepared, formula_pages=formula_pages, metadata=metadata,
        independent_ocr_run_dir=None)


def assemble_document(
    page_document: dict[str, Any],
    *,
    formula_pages: dict[int, list[dict[str, Any]]] | None = None,
    metadata: dict[str, Any] | None = None,
    independent_ocr_run_dir: str | None = None,
) -> dict[str, Any]:
    """Assemble a V2 CanonicalDocument dict from a V1 document.

    Args:
        page_document: a single V1 document dict (one ``corpus-*-documents.jsonl``
            row). Only ``sha256`` / ``file_name`` / ``pages`` / ``metadata`` /
            ``processing_run_id`` / ``config_hash`` are consumed.
        formula_pages: optional mapping ``physical_page -> list[formula region]``
            (the ``result.pages[0].regions`` of the per-page formula cache,
            as returned by :func:`load_formula_pages`). When ``None`` the
            document has no FormulaElements and NO ``formula_ledger`` marker
            (legacy contract). When given, the detected formula regions of the
            page-intermediate are strictly reconciled against the cache and
            every detected region closes with exactly one outcome; the result
            carries the ``formula_ledger`` marker (T04).
        metadata: optional registry metadata override; merged on top of the V1
            document-level ``metadata``.

    Returns:
        A V2 CanonicalDocument dict with placeholder canonical IDs. The caller
        must run :func:`compute_ids` then :func:`validate_document` before
        emitting the artifact.
    """
    _formula_ledger_produced = formula_pages is not None
    formula_pages = formula_pages or {}
    fusion_summary = page_document.get("_v3_ocr_summary")
    if independent_ocr_run_dir is not None:
        page_document, fusion_summary = fuse_document_pages(
            page_document, run_dir=independent_ocr_run_dir)
    page_rows = page_document.get("pages") or []
    sha = (page_document.get("sha256") or "").lower()
    page_count = len(page_rows)

    # T04-R2: the doc's formula ENGINE registry is built from the cache run
    # provenance actually consumed -- distinct (engine name, version) pairs are
    # registered deterministically (formula-1, formula-2, ...). No engine is
    # invented when no formula cache was consumed; nothing is hardcoded.
    formula_engine_ids: dict[tuple[str, str], str] = {}
    formula_engine_order: list[tuple[str, str, str]] = []
    for regions in formula_pages.values():
        for region in regions:
            meta = region.get("_formula_run") or {}
            model = str(meta.get("formula_model") or "")
            name = model.rsplit(":", 1)[-1] if model else "unknown"
            version = str(meta.get("paddleocr_version") or "unknown")
            key = (name, version)
            if key not in formula_engine_ids:
                eid = f"formula-{len(formula_engine_ids) + 1}"
                formula_engine_ids[key] = eid
                formula_engine_order.append((eid, name, version))
            region["_formula_engine_id"] = formula_engine_ids[key]

    # T04-R2: duplicate/conflicting SOURCE region ids are a hard failure --
    # two detected regions claiming the same identity would make the lossless
    # element->region traceability ambiguous.
    if _formula_ledger_produced:
        seen_source_ids: dict[str, int] = {}
        for pno_pre, page_row in enumerate(page_rows, 1):
            page_no = int(page_row.get("physical_page") or pno_pre)
            for region in page_row.get("regions") or []:
                if region.get("kind") != "formula":
                    continue
                rid = region.get("region_id")
                if isinstance(rid, str) and rid.strip():
                    if rid in seen_source_ids:
                        raise ValueError(
                            f"duplicate/conflicting formula source region_id "
                            f"{rid!r} on pages {seen_source_ids[rid]} and "
                            f"{page_no} (detection disagrees with itself)")
                    seen_source_ids[rid] = page_no

    # T04: strict three-way reconciliation between the page-intermediate's
    # DETECTED formula regions, the CACHE-loaded recognition results, and the
    # ASSEMBLED elements/outcomes. One outcome per detected region -- missing,
    # extra, duplicate or gate-rejected regions are never silently skipped.
    formula_outcomes: list[dict[str, Any]] = []
    pending_formula_outcomes: list[dict[str, Any]] = []
    cache_total = sum(len(v) for v in formula_pages.values())
    cache_visited_pages: set[int] = set()
    # T05: figure outcome ledger -- one entry per DETECTED image region,
    # whatever happened to it (asset verified -> completed element; missing /
    # unverifiable asset -> explicit failed close; unusable coordinates ->
    # unsupported). No cache exists for figures: the asset verification IS the
    # recognition step, so reconciliation is unconditional.
    figure_outcomes: list[dict[str, Any]] = []
    pending_figure_outcomes: list[dict[str, Any]] = []

    # T05: duplicate/conflicting figure SOURCE region ids are a hard failure
    # (same lossless-traceability contract as the formula source ids).
    seen_figure_source_ids: dict[str, int] = {}
    for pno_pre, page_row in enumerate(page_rows, 1):
        page_no = int(page_row.get("physical_page") or pno_pre)
        for region in page_row.get("regions") or []:
            if region.get("kind") != "image":
                continue
            rid = region.get("region_id")
            if isinstance(rid, str) and rid.strip():
                if rid in seen_figure_source_ids:
                    raise ValueError(
                        f"duplicate/conflicting figure source region_id "
                        f"{rid!r} on pages {seen_figure_source_ids[rid]} and "
                        f"{page_no} (detection disagrees with itself)")
                seen_figure_source_ids[rid] = page_no

    # Strip the legacy region-level ``publishable`` flag. It was a review-subsystem
    # status that unified_pdf no longer writes; older page-intermediate caches may
    # still carry it. It is NOT a V2 contract field and assembly never gated on it,
    # but we pop it here so it cannot leak into V2 artifacts via ``ref``.
    for page_row in page_rows:
        for region in page_row.get("regions") or []:
            region.pop("publishable", None)

    # 1. Build per-page ordered item lists + global reading-order slots. -------
    # T02 P2: pre-compute the OCR engine registry so every page (including blank
    # pages with no elements) can carry a resolving engine_id. Distinct OCR
    # engine identities (parser_name + engine_version) are registered separately
    # (ocr-1, ocr-2, ...); a missing engine_version on an old cache is recorded
    # as "unknown" with a reason, never guessed as a real version.
    doc_config_hash = (page_document.get("config_hash") or "").lower()
    ocr_engine_ids: dict[tuple[str, str], str] = {}
    ocr_engine_order: list[tuple[str, str, str]] = []  # (engine_id, name, version)
    for page_row in page_rows:
        route = (page_row.get("extraction_route") or "").strip()
        if route not in ("full_ocr", "hybrid", "scanned_with_text_layer"):
            continue
        name = (page_row.get("parser_name") or "").strip() or "paddleocr-text"
        version = (page_row.get("engine_version") or "").strip()
        if not version:
            version = "unknown"  # old cache: do not guess the real version
        key = (name, version)
        if key not in ocr_engine_ids:
            eid = f"ocr-{len(ocr_engine_ids) + 1}"
            ocr_engine_ids[key] = eid
            ocr_engine_order.append((eid, name, version))

    global_slots: list[_PageItem] = []
    pages_meta: list[dict[str, Any]] = []
    page_by_number: dict[int, dict[str, Any]] = {}
    needs_unknown_engine = False  # set when any page has a missing/unknown route
    # T03: structured-region outcome ledger -- one entry per detected table
    # region, whether it yielded a TableElement or failed. Reconciles
    # detected regions == successful outcomes + failed outcomes.
    structured_outcomes: list[dict[str, Any]] = []
    for idx, page_row in enumerate(page_rows):
        pno = int(page_row.get("physical_page") or (idx + 1))
        page_by_number[pno] = page_row
        width = float(page_row.get("width") or 0.0)
        height = float(page_row.get("height") or 0.0)
        rotation = int(page_row.get("rotation") or 0) % 360
        if rotation not in (0, 90, 180, 270):
            rotation = 0
        parse_status, parse_error = _parse_status(page_row)
        # T02: persist the page's real extraction route + decision so a chunk can
        # trace back to page-intermediate without re-reading it. These fields are
        # digest-excluded (see canonical_document._DIGEST_EXCLUDED_PAGE_FIELDS),
        # so carrying them does not change the identity of published artifacts.
        route = (page_row.get("extraction_route") or "").strip() or None
        if route not in (
            "native", "full_ocr", "hybrid", "scanned_with_text_layer", "failed",
        ):
            route = None
        decision = (page_row.get("decision_status") or "").strip() or None
        if decision not in ("approved", "quarantine", "failed"):
            decision = None
        decision_reasons = list(page_row.get("decision_reasons") or [])
        ocr_confidence = page_row.get("ocr_confidence")
        if ocr_confidence is not None:
            try:
                ocr_confidence = round(float(ocr_confidence), 6)
            except (TypeError, ValueError):
                ocr_confidence = None
        engine_name = (page_row.get("parser_name") or "").strip()
        engine_version = (page_row.get("engine_version") or "").strip()
        # T02 P2: page-level engine_id (resolves to generation.engines) + config.
        # Native -> native-1; OCR -> the registered ocr-N for its (name, version);
        # failed -> None (no engine produced text); missing/unknown route ->
        # unknown-1 (honest: do NOT guess native). Only an explicit route value
        # maps to a real engine.
        if route in ("full_ocr", "hybrid", "scanned_with_text_layer"):
            ver = engine_version or "unknown"
            page_engine_id = ocr_engine_ids.get((engine_name or "paddleocr-text", ver))
        elif route == "native":
            page_engine_id = _NATIVE_ENGINE_ID
        elif route == "failed":
            page_engine_id = None
        else:  # route is None (missing/unknown)
            page_engine_id = _UNKNOWN_ENGINE_ID
            needs_unknown_engine = True
        page_config_hash = (page_row.get("layout_config_hash") or "").lower()
        if not page_config_hash or len(page_config_hash) != 64:
            page_config_hash = doc_config_hash or None
        # T04: reconcile this page's detected formula regions against the
        # formula cache BEFORE the parse-status gate -- a detected region on a
        # blank/failed page must still close with an explicit outcome.
        page_formula_outcomes, consumable, page_pending = _reconcile_formula_page(
            page_row, pno, sha, formula_pages.get(pno))
        cache_visited_pages.add(pno)
        formula_outcomes.extend(page_formula_outcomes)
        pending_formula_outcomes.extend(page_pending)
        # T05: reconcile this page's detected image regions BEFORE the
        # parse-status gate -- a detected figure on a blank/failed page must
        # still close with an explicit outcome.
        page_figure_outcomes, figure_consumable, page_figure_pending = (
            _reconcile_figure_page(page_row, pno, sha))
        figure_outcomes.extend(page_figure_outcomes)
        pending_figure_outcomes.extend(page_figure_pending)
        pages_meta.append({
            "physical_page": pno,
            "printed_label": page_row.get("display_page_label"),
            "width": width,
            "height": height,
            "unit": "pt",
            "rotation_degrees": rotation,
            "parse_status": parse_status,
            "parse_error": parse_error,
            "extraction_route": route,
            "decision_status": decision,
            "decision_reasons": decision_reasons,
            "engine_id": page_engine_id,
            "confidence": ocr_confidence,
            "config_hash": page_config_hash,
        })
        if parse_status != "parsed":
            continue  # blank/failed pages contribute no slots
        items = _collect_page_items(
            page_row, pno, width, height, consumable,
            figure_regions=figure_consumable,
            route=route,
            decision=decision,
            decision_reasons=decision_reasons,
            ocr_confidence=ocr_confidence,
            engine_name=engine_name,
            engine_version=engine_version,
            page_engine_id=page_engine_id,
        )
        # A table region with an invalid/absent bbox produces no slot, but it
        # is a DETECTED region: it must still close with exactly one outcome
        # (unsupported), never be silently omitted (T03 reconciliation).
        for region in page_row.get("regions") or []:
            if region.get("kind") != "table":
                continue
            if _coerce_bbox(region.get("bbox")) is not None:
                continue
            issues = list(region.get("issues") or [])
            if "invalid_detection_coordinates" not in issues:
                issues.append("invalid_detection_coordinates")
            structured_outcomes.append(_table_outcome(
                region, pno, element_id=None, execution_status="unsupported",
                quality_status="needs_review", issues=issues,
                attempts=_build_attempt_ledger(
                    region, failure_reason="invalid_detection_coordinates"),
                selection_basis=None, admissible_ids=[],
                selected_result_id=None, selected_attempt_id=None))
        # OCR line elements still follow the upstream page stream here.  Join
        # lines inside a paragraph before column detection: once a page is
        # (even legitimately) reordered column-major, adjacent lines from one
        # source region may no longer be neighbours and cannot be recovered.
        if route in ("full_ocr", "hybrid", "scanned_with_text_layer") and not page_document.get("_middle_layer_v3"):
            items = _reconstruct_ocr_paragraphs(items)
        layout = detect_columns(
            [it.bbox for it in items], page_width=width, page_height=height
        )
        ordered_items = [items[order_idx] for order_idx in layout.reading_order]
        global_slots.extend(ordered_items)

    # Assign global source_index (= position in logical reading order).
    for i, slot in enumerate(global_slots):
        slot.source_index = i

    # 1b. Header / footer recurring-text detection. ---------------------------
    # A short text recurring in the same top/bottom band across many pages is
    # a running header/footer; mark it so _infer_text_role returns the right
    # role and the chunker drops it via _IGNORED_ROLES.
    if not page_document.get("_middle_layer_v3"):
        _mark_recurring_header_footer(global_slots, page_by_number)

    # 2. Heading detection over the global text stream. -----------------------
    # Detect TOC pages from the complete text stream before pre-filtering
    # heading candidates. OCR frequently separates an entry's title and page
    # number into distinct blocks; page-level evidence is therefore required.
    # Use the upstream line/block elements, not the later region-combined
    # slots: title/page-number pairs are often separate OCR blocks and would
    # otherwise be hidden by region aggregation before TOC detection.
    toc_inputs: list[HeadingInput] = []
    toc_index = 0
    for page_row in page_document.get("pages") or []:
        for raw_element in page_row.get("elements") or []:
            raw_text = str(raw_element.get("text") or "").strip()
            raw_bbox = _coerce_bbox(raw_element.get("bbox"))
            if not raw_text or raw_bbox is None:
                continue
            toc_inputs.append(HeadingInput(
                text=raw_text,
                bbox=tuple(raw_bbox),
                source_index=toc_index,
                physical_page=int(page_row["physical_page"]),
            ))
            toc_index += 1
    toc_pages = detect_toc_pages(toc_inputs)
    heading_inputs: list[HeadingInput] = []
    explicit_v3_headings: list[HeadingNode] = []
    for slot in global_slots:
        if slot.kind != "text":
            continue
        if slot.page in toc_pages:
            continue
        v3_heading = slot.extra.get("v3_heading")
        if isinstance(v3_heading, dict):
            explicit_v3_headings.append(HeadingNode(
                label=str(v3_heading.get("label") or ""),
                title=v3_heading.get("title"),
                level=int(v3_heading.get("level") or 1),
                confidence=0.95,
                source_index=slot.source_index,
            ))
            continue
        if slot.extra.get("v3_block"):
            continue
        candidate = _heading_candidate_text(
            slot.text, bbox=slot.bbox,
            page_width=float(page_by_number.get(slot.page, {}).get("width") or 0.0),
        )
        if candidate is not None:
            heading_inputs.append(
                HeadingInput(
                    text=candidate,
                    bbox=tuple(slot.bbox),
                    source_index=slot.source_index,
                    physical_page=slot.page,
                )
            )
    headings = sorted(
        explicit_v3_headings + infer_headings(heading_inputs),
        key=lambda heading: heading.source_index,
    )
    heading_by_index = {h.source_index: h for h in headings}

    # 3. Build V2 elements. ----------------------------------------------------
    span_counter = _Counter("s")
    elem_counter = _Counter("e")
    elements: list[dict[str, Any]] = []
    # T04-R2: parsed-page map for projecting relation-source spans (a span on a
    # blank/failed page would violate invariant 9, so only parsed pages count).
    parsed_page_numbers = {
        int(pm["physical_page"]) for pm in pages_meta
        if pm.get("parse_status") == "parsed"}
    parsed_page_dims = {
        int(pm["physical_page"]): (float(pm["width"]), float(pm["height"]))
        for pm in pages_meta}
    # Track tables by region id for cross-page `continues` link resolution
    # (explicit relation target -> element id; order-independent).
    table_registry: dict[tuple[str, str], str] = {}
    # (element, region, outcome) triples for the phase-2 continuation pass.
    table_element_regions: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    # T04-R2: (element, outcome) pairs for the phase-2 cross-page relation
    # resolution pass (target region -> element id; order-independent).
    formula_element_regions: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    # First pass: identify consumed text elements (formula/table/figure/caption)
    # so they are not also emitted as TextElements. A text element is consumed
    # by a structured region only when it is SUBSTANTIALLY CONTAINED in that
    # region (>= 60% of its area inside) -- a partial edge overlap must NOT
    # delete an otherwise-independent paragraph (T03 dedup). The consumed
    # source element ids are recorded on the structured element's source_spans
    # provenance so the origin is traceable, not silently dropped.
    # T05-R2 REJECT: geometric containment consumes text ONLY for table and
    # formula hosts (their T03/T04 contracts are unchanged). A figure NEVER
    # consumes text by containment -- text inside a figure bbox stays an
    # independent text element unless the PI caption provides an explicit,
    # uniquely checkable identity for it (see below).
    consumed_indices: set[int] = set()
    # Consumed-text traceability (REJECT requirement 6): for each consuming
    # structured slot, record the identity of every text element it consumes
    # (upstream element id, page, bbox) so the dedup is traceable, not a
    # silent drop.
    consumed_by: dict[int, list[dict[str, Any]]] = {}
    # T09: slot.source_index -> emitted element_id (text + formula), so the
    # explanation-binding pass can resolve bound slots to real element ids.
    slot_element_ids: dict[int, str] = {}
    formula_slot_elements: list[tuple[_PageItem, dict[str, Any]]] = []
    # T05-R2: figure-outcome issues raised by the caption-binding pass below,
    # collected per figure slot and applied sorted (input-order independent).
    figure_binding_issues: dict[int, set[str]] = {}
    _CONTAINMENT_RATIO = 0.6
    for slot in global_slots:
        if slot.kind not in ("table", "formula"):
            continue
        for t_slot in global_slots:
            if t_slot.kind != "text" or t_slot.page != slot.page:
                continue
            if _bbox_containment_ratio(slot.bbox, t_slot.bbox) >= _CONTAINMENT_RATIO:
                consumed_indices.add(t_slot.source_index)
                consumed_by.setdefault(slot.source_index, []).append({
                    "element_id": (t_slot.ref or {}).get("element_id"),
                    "physical_page": t_slot.page,
                    "bbox": list(t_slot.bbox),
                })
    # Table caption text elements are consumed by their table (T03): the
    # "表N" rule binds to TABLES ONLY -- a figure is never a host, so a
    # "表N" text on a figure-only page survives as an independent element.
    for slot in global_slots:
        if slot.kind != "text":
            continue
        if slot.source_index in consumed_indices:
            continue
        s = " ".join((slot.text or "").split())
        if _TABLE_CAPTION_RE.match((slot.text or "").strip()):
            hosts = [sl for sl in global_slots
                     if sl.kind == "table" and sl.page == slot.page]
            if hosts:
                consumed_indices.add(slot.source_index)
                consumed_by.setdefault(hosts[0].source_index, []).append({
                    "element_id": (slot.ref or {}).get("element_id"),
                    "physical_page": slot.page,
                    "bbox": list(slot.bbox),
                })
        elif s:
            # T05-R2: a figure consumes its caption text ONLY when the PI
            # caption/relation carries an explicit identity (element_id, or a
            # same-page bbox) that uniquely binds the text slot. Text equality
            # alone is NOT consumption: with no anchor the text stays an
            # independent element and the unresolved state is recorded on the
            # figure outcome; with several equally-bound hosts NOTHING is
            # consumed (never first-wins) and the ambiguity is recorded.
            figure_hosts = [
                sl for sl in global_slots
                if sl.kind == "figure" and sl.page == slot.page
                and _figure_pi_caption_text(sl.ref) == s]
            if figure_hosts:
                bound: list[tuple[_PageItem, str]] = []
                for fh in figure_hosts:
                    basis = _figure_caption_anchor_basis(fh.ref, slot)
                    if basis:
                        bound.append((fh, basis))
                if len(bound) == 1:
                    host, basis = bound[0]
                    consumed_indices.add(slot.source_index)
                    consumed_by.setdefault(host.source_index, []).append({
                        "element_id": (slot.ref or {}).get("element_id"),
                        "physical_page": slot.page,
                        "bbox": list(slot.bbox),
                        "basis": basis,
                    })
                else:
                    if len(bound) > 1:
                        label = "ambiguous"
                        targets = [fh for fh, _ in bound]
                    elif len(figure_hosts) > 1:
                        label = "ambiguous"
                        targets = figure_hosts
                    else:
                        label = "unresolved"
                        targets = figure_hosts
                    eid = (slot.ref or {}).get("element_id")
                    issue = (f"figure_caption_binding_{label}:{eid}"
                             if eid else f"figure_caption_binding_{label}")
                    for fh in targets:
                        figure_binding_issues.setdefault(
                            fh.source_index, set()).add(issue)
    # Apply the binding issues deterministically (sorted, deduplicated --
    # the same text slot may be reported against several figures): the
    # outcome dict is the same object that later closes, so the element's
    # quality_issues projection picks these up too.
    for idx, issues in figure_binding_issues.items():
        outcome = (global_slots[idx].ref or {}).get("_figure_outcome")
        if outcome is not None:
            outcome["issues"] = sorted(set(outcome.get("issues") or []) | issues)
    for idx, identities in consumed_by.items():
        global_slots[idx].extra["consumed_sources"] = identities

    for slot in global_slots:
        if slot.kind == "text":
            if slot.source_index in consumed_indices:
                continue
            el = _build_text_element(slot, heading_by_index, headings, span_counter, elem_counter)
            if el is not None:
                elements.append(el)
                slot_element_ids[slot.source_index] = el["element_id"]
        elif slot.kind == "formula":
            el = _build_formula_element(
                slot, global_slots, heading_by_index, headings, span_counter,
                elem_counter, parsed_pages=parsed_page_numbers,
                page_dims=parsed_page_dims,
            )
            outcome = (slot.ref or {}).get("_formula_outcome")
            if el is not None:
                elements.append(el)
                slot_element_ids[slot.source_index] = el["element_id"]
                formula_slot_elements.append((slot, el))
                formula_element_regions.append((el, outcome))
                if outcome is not None:
                    # Close the pending outcome as COMPLETED: bound to the
                    # emitted element, with exactly one selected completed
                    # attempt of the selected (stable-identity) result.
                    identity = (slot.ref or {}).get("_formula_identity")
                    outcome["element_id"] = el["element_id"]
                    outcome["execution_status"] = "completed"
                    outcome["selection_basis"] = "single_admissible"
                    outcome["admissible_result_ids"] = [identity]
                    outcome["selected_result_id"] = identity
                    outcome["selected_attempt_id"] = _mark_selected_attempt(
                        outcome["attempts"], identity)
                    if outcome in pending_formula_outcomes:
                        pending_formula_outcomes.remove(outcome)
                    formula_outcomes.append(outcome)
            elif outcome is not None:
                # Defensive: an element failed to build for a gate-passing
                # region -- an explicit failure, never silence.
                outcome["execution_status"] = "failed"
                if "empty_formula_recognition" not in outcome["issues"]:
                    outcome["issues"].append("empty_formula_recognition")
                if outcome in pending_formula_outcomes:
                    pending_formula_outcomes.remove(outcome)
                formula_outcomes.append(outcome)
        elif slot.kind == "table":
            el, outcome = _build_table_element(
                slot, global_slots, heading_by_index, headings,
                span_counter, elem_counter,
            )
            if outcome is not None:
                structured_outcomes.append(outcome)
            if el is not None:
                elements.append(el)
                # Phase 1 of continuation resolution: register the complete
                # region -> element mapping (forward references resolve later).
                rid = (slot.ref or {}).get("region_id")
                if rid:
                    table_registry[("__region__", rid)] = el["element_id"]
                table_element_regions.append((el, slot.ref or {}, outcome))
        elif slot.kind == "figure":
            el = _build_figure_element(
                slot, global_slots, heading_by_index, headings, span_counter,
                elem_counter, parsed_pages=parsed_page_numbers,
                page_dims=parsed_page_dims,
            )
            outcome = (slot.ref or {}).get("_figure_outcome")
            if el is not None:
                elements.append(el)
                if outcome is not None:
                    # Close the pending outcome as COMPLETED: bound to the
                    # emitted element, with exactly one selected completed
                    # attempt of the stable-identity result. Completed means
                    # ONLY "the real crop asset was faithfully assembled" --
                    # quality stays needs_review (D1).
                    identity = (slot.ref or {}).get("_figure_identity")
                    outcome["element_id"] = el["element_id"]
                    outcome["execution_status"] = "completed"
                    outcome["selection_basis"] = "single_admissible"
                    outcome["admissible_result_ids"] = [identity]
                    outcome["selected_result_id"] = identity
                    outcome["selected_attempt_id"] = _mark_selected_attempt(
                        outcome["attempts"], identity)
                    if outcome in pending_figure_outcomes:
                        pending_figure_outcomes.remove(outcome)
                    figure_outcomes.append(outcome)
            elif outcome is not None:
                # Defensive: an element failed to build for a verified-asset
                # region -- an explicit failure, never silence.
                outcome["execution_status"] = "failed"
                if "figure_content_unavailable" not in outcome["issues"]:
                    outcome["issues"].append("figure_content_unavailable")
                if outcome in pending_figure_outcomes:
                    pending_figure_outcomes.remove(outcome)
                figure_outcomes.append(outcome)

    # 3b. Cross-page continuation resolution (phase 2 of 2, REJECT fix #4). ---
    # The region -> element registry is now COMPLETE, so explicit relations
    # resolve regardless of page order (forward references included). Only
    # explicit ``explicit_continued_table_number`` relations are honoured --
    # never page adjacency or a repeated table number. A missing target, a
    # self reference, conflicting / multi-candidate relations, or missing /
    # conflicting table-number evidence produce an explicit issue on BOTH the
    # element and its outcome; no dangling link and no silent drop.
    table_elements_by_id = {
        t["element_id"]: t for t, _r, _o in table_element_regions
    }
    for el, region, outcome in table_element_regions:
        candidate_targets: list[str] = []
        rel_issues: list[str] = []
        own_region_id = region.get("region_id")
        for rel in region.get("relations") or []:
            if rel.get("kind") != "explicit_continued_table_number":
                continue
            target_region_id = rel.get("target_region_id")
            if not target_region_id:
                rel_issues.append("continuation_relation_missing_target_region_id")
                continue
            if target_region_id == own_region_id:
                rel_issues.append("continuation_relation_self_reference")
                continue
            target_elem_id = table_registry.get(("__region__", target_region_id))
            if target_elem_id is None:
                rel_issues.append(f"continuation_target_missing:{target_region_id}")
                continue
            candidate_targets.append(target_elem_id)
        distinct = sorted(set(candidate_targets))
        links: list[dict[str, Any]] = []
        if len(distinct) > 1:
            rel_issues.append(
                "continuation_relation_conflict:" + ",".join(distinct))
        elif len(distinct) == 1:
            # A continues chain needs THREE pieces of evidence: the explicit
            # relation, a resolvable target, AND consistent explicit
            # table-number evidence on both sides (REJECT issue 4). A missing
            # or conflicting table number means the numbering was not
            # recovered -- the chain is not built and the gap is recorded.
            own_number = ((el.get("content") or {}).get("table_number") or "").strip()
            target_el = table_elements_by_id.get(distinct[0])
            target_number = (
                ((target_el or {}).get("content") or {}).get("table_number") or ""
            ).strip()
            if not own_number or not target_number:
                rel_issues.append("continuation_table_number_missing")
            elif own_number != target_number:
                rel_issues.append(
                    f"continuation_table_number_conflict:"
                    f"{own_number}!={target_number}")
            else:
                links.append({"type": "continues", "target_element_id": distinct[0]})
        el["links"] = links
        if rel_issues:
            for issue in rel_issues:
                if issue not in el["quality_issues"]:
                    el["quality_issues"].append(issue)
            if outcome is not None:
                for issue in rel_issues:
                    if issue not in outcome["issues"]:
                        outcome["issues"].append(issue)

    # 3b'. T04-R2: structured cross-page formula relation resolution. The
    # element->region registry is complete, so an explicit target region
    # reference resolves regardless of page order. A resolved target becomes
    # an auditable ``related`` link + ``target_element_id`` on the record; a
    # missing/self target is an explicit issue on the element AND its outcome
    # (the relation stays needs_review) -- never adjacency guessing and never
    # a dangling link.
    formula_registry: dict[str, str] = {}
    for el, _outcome in formula_element_regions:
        for rid in el.get("source_region_ids") or []:
            formula_registry.setdefault(rid, el["element_id"])
    for el, outcome in formula_element_regions:
        for rel in el.get("cross_page_relations") or []:
            target = rel.get("target") or {}
            target_rid = target.get("region_id")
            if not target_rid:
                continue  # no explicit target region: the record stays as-is
            target_elem_id = formula_registry.get(target_rid)
            if target_elem_id is None or target_elem_id == el["element_id"]:
                issue = f"cross_page_relation_unresolved_target:{target_rid}"
                if issue not in el["quality_issues"]:
                    el["quality_issues"].append(issue)
                if outcome is not None and issue not in outcome["issues"]:
                    outcome["issues"].append(issue)
                continue
            rel["target_element_id"] = target_elem_id
            el["links"].append(
                {"type": "related", "target_element_id": target_elem_id})

    # 3c. T04 formula ledger finalisation. -------------------------------------
    # Pending (gate-passing) outcomes join the ledger here: completed ones were
    # closed with their element in the pass above; one whose element was never
    # built (blank/failed/not-parsed page produced no slots) closes as an
    # explicit failure -- never silence, never "not_detected".
    formula_outcomes.extend(pending_formula_outcomes)
    for outcome in pending_formula_outcomes:
        if outcome["execution_status"] != "completed":
            outcome["execution_status"] = "failed"
            if "formula_page_not_parsed" not in outcome["issues"]:
                outcome["issues"].append("formula_page_not_parsed")
    # Cache regions that no detected region could consume: the page never
    # existed in the page-intermediate (within-page extras already hard-fail
    # in the matcher). Detection and cache must agree exactly (T04).
    unvisited = sorted(set(formula_pages) - cache_visited_pages)
    if unvisited and any(formula_pages[p] for p in unvisited):
        raise ValueError(
            "formula cache regions without a detected source region: "
            f"pages {unvisited} absent from the page-intermediate "
            f"(cache_total={cache_total}) for sha={sha[:12]}")
    # T05 figure ledger finalisation. Pending (asset-verified) outcomes join
    # the ledger here: completed ones were closed with their element in the
    # pass above; one whose element was never built (blank/failed/not-parsed
    # page produced no slots) closes as an explicit failure -- never silence,
    # never "not_detected".
    figure_outcomes.extend(pending_figure_outcomes)
    for outcome in pending_figure_outcomes:
        if outcome["execution_status"] != "completed":
            outcome["execution_status"] = "failed"
            if "figure_page_not_parsed" not in outcome["issues"]:
                outcome["issues"].append("figure_page_not_parsed")
    # T04-R3: sync the SHARED provenance of completed outcomes with their
    # elements -- the element projection is authoritative (its relation
    # records carry the source-span ids and resolved target element ids that
    # only exist after the element pass), and the outcome must carry exactly
    # the same crop assets and structured relations. The validator enforces
    # this equality, so the producer performs it explicitly here.
    built_elements_by_id = {el["element_id"]: el for el in elements}
    for outcome in formula_outcomes + figure_outcomes:
        if outcome["execution_status"] != "completed" or not outcome.get("element_id"):
            continue
        el = built_elements_by_id.get(outcome["element_id"])
        if el is None:
            continue
        outcome["crop_assets"] = [dict(c) for c in el.get("crop_assets") or []]
        outcome["cross_page_relations"] = [
            dict(r) for r in el.get("cross_page_relations") or []]

    # 3d. T09 formula/explanation association (four-requirement primary
    # binding + explicit-only cross-page secondary binding). Runs AFTER the
    # element pass so every explanation slot has a real element_id, and the
    # relation records carry their resolved projections. The stored
    # ``context_text`` is NOT touched (content identity stays stable); the
    # bindings are the structural record on top of it.
    n_explanation_bindings = 0
    elements_by_id_t09 = {el["element_id"]: el for el in elements}
    for fx_slot, el in formula_slot_elements:
        bindings, issues = _resolve_explanation_bindings(
            fx_slot, global_slots, slot_element_ids, headings)
        cross_bindings, cross_issues = _resolve_cross_page_explanation_bindings(
            el, elements_by_id_t09)
        bindings.extend(cross_bindings)
        issues.extend(cross_issues)
        if bindings:
            el["explanation_bindings"] = bindings
            n_explanation_bindings += len(bindings)
        for issue in issues:
            if issue not in el["quality_issues"]:
                el["quality_issues"].append(issue)
        # T09 remediation: a formula with NO primary binding must not retain
        # the geometry-guessed context_text / context spans. Its walk-window
        # candidates are marked so the chunker keeps each explanation out of
        # the surrounding prose (independent text, projected exactly once).
        if not any(b["role"] == "primary" for b in bindings):
            candidates, _ = _walk_formula_context(fx_slot, global_slots)
            candidate_ids = [slot_element_ids[t.source_index]
                             for t in candidates
                             if t.source_index in slot_element_ids]
            _drop_unbound_formula_context(el, candidate_ids, elements_by_id_t09)
    # Canonical ledger order: table outcomes in reading order (T03), formula
    # outcomes in stable region-identity order (T04), figure outcomes in
    # stable region-identity order (T05) -- input-order independent.
    formula_outcomes.sort(key=lambda o: o["region_id"])
    structured_outcomes.extend(formula_outcomes)
    figure_outcomes.sort(key=lambda o: o["region_id"])
    structured_outcomes.extend(figure_outcomes)

    # 4. Source / metadata / generation. --------------------------------------
    source = _build_source(page_document, page_count)
    doc_metadata = _build_metadata(page_document, metadata)
    generation = _build_generation(
        page_document, formula_pages, ocr_engine_order, needs_unknown_engine,
        formula_engine_order, fusion_summary,
        paragraph_reconstruction=any(
            slot.extra.get("paragraph_reconstruction") for slot in global_slots),
    )

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
        "structured_outcomes": structured_outcomes,
        # T04: the producer always declares formula reconciliation semantics
        # (formula_ledger marker) whenever a formula cache was consumed, so
        # the acceptance gate cannot be bypassed by dropping the outcomes.
        # ``formula_pages=None`` keeps the legacy (unmarked) contract.
        **({"formula_ledger": {"version": "t04-1",
                               "outcome_count": len(formula_outcomes)}}
           if _formula_ledger_produced else {}),
        # T05: the figure reconciliation is unconditional (no cache seam
        # exists), so every new assembly declares it via the figure_ledger
        # marker; legacy artifacts without the marker keep validating.
        **{"figure_ledger": {"version": "t05-1",
                             "outcome_count": len(figure_outcomes)}},
        # T09: the formula/explanation association marker is unconditional on
        # every new assembly (producer declares T09 semantics). Audit-only:
        # legacy artifacts without it keep byte-identical digests, and its
        # presence routes the chunker to the v2 formula-binding projection.
        **{"formula_context_ledger": {"version": "t09-1",
                                      "binding_count": n_explanation_bindings}},
    }


def load_formula_pages(
    sha: str, pages: Iterable[int], *, cache_dir: str
) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Strictly load per-page formula regions from the V1 formula cache (T04).

    Reads ``<cache_dir>/<sha>-<page>.json`` for each requested page and returns
    ``(formula_pages, page_failures)`` where ``formula_pages`` maps
    ``physical_page -> list[region dict]`` (the ``result.pages[0].regions``
    entries, annotated with the file-level ``_formula_run`` provenance).

    T04 reconciliation contract -- silence is never acceptable:

    * missing cache DIRECTORY -> ``FileNotFoundError`` (hard fail: assembly
      would silently produce 0 formula elements);
    * corrupt JSON, wrong ``sha256``, a ``config_hash`` that does not belong to
      the run directory, a non-formula region, invalid region coordinates, a
      non-string ``raw_latex``, multiple page entries, or a region/file whose
      ``physical_page`` disagrees with the requested page -> ``ValueError``
      (hard fail: the cache is damaged or foreign and cannot be trusted);
    * a MISSING single-page file is an *auditable per-page failure* (recorded
      in ``page_failures``), NOT a silent skip: whether that page needed
      formula results is adjudicated by the detected-region reconciliation in
      :func:`assemble_document` (a detected region with no cache result closes
      as an explicit failed outcome, never as a silent drop).
    """
    import json
    import os

    run_id = os.path.basename(os.path.normpath(cache_dir))
    if not run_id:
        raise FileNotFoundError(f"formula cache directory not found: {cache_dir!r}")
    if not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"formula cache directory not found: {cache_dir}\n"
            f"  recognize_corpus_formulas was not run for run-id {run_id!r}; "
            f"run build_corpus.py (which orchestrates it) or "
            f"scripts/recognize_corpus_formulas.py first.")
    sha = (sha or "").lower()

    out: dict[int, list[dict[str, Any]]] = {}
    failures: list[dict[str, Any]] = []
    for pno in pages:
        pno = int(pno)
        path = os.path.join(cache_dir, f"{sha}-{pno}.json")
        if not os.path.isfile(path):
            failures.append({"physical_page": pno,
                             "reason": "formula_cache_file_missing"})
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"formula cache corrupt: {path} ({type(exc).__name__})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"formula cache corrupt: {path} is not an object")
        if str(raw.get("sha256") or "").lower() != sha:
            raise ValueError(
                f"formula cache {path}: sha256 {raw.get('sha256')!r} does not "
                f"match requested document {sha!r}")
        config_hash = raw.get("config_hash")
        if (not isinstance(config_hash, str)
                or not config_hash.lower().startswith(run_id.lower())):
            raise ValueError(
                f"formula cache {path}: config_hash {config_hash!r} does not "
                f"belong to run {run_id!r}")
        result = raw.get("result") or {}
        page_entries = result.get("pages") or []
        if len(page_entries) != 1:
            raise ValueError(
                f"formula cache {path}: expected exactly one page entry, "
                f"got {len(page_entries)}")
        entry = page_entries[0]
        entry_page = entry.get("physical_page")
        if entry_page is not None and int(entry_page) != pno:
            raise ValueError(
                f"formula cache {path}: physical_page {entry_page!r} does not "
                f"match requested page {pno}")
        # File-level recognition provenance (annotation is private to the
        # loader; consumed by the outcome builder, never serialised verbatim).
        run_meta = {
            "config_hash": config_hash.lower(),
            "profile": result.get("profile"),
            "formula_model": result.get("formula_model"),
            "paddleocr_version": (result.get("package_versions") or {}).get("paddleocr"),
        }
        regions: list[dict[str, Any]] = []
        for region in entry.get("regions") or []:
            if not isinstance(region, dict) or region.get("kind") != "formula":
                raise ValueError(
                    f"formula cache {path}: non-formula region entry "
                    f"{(region or {}).get('kind') if isinstance(region, dict) else region!r}")
            bbox = region.get("bbox")
            if (not isinstance(bbox, list) or len(bbox) != 4
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               for v in bbox)
                    or not (bbox[0] <= bbox[2] and bbox[1] <= bbox[3])):
                raise ValueError(
                    f"formula cache {path}: invalid region coordinates {bbox!r}")
            region_page = region.get("physical_page")
            if region_page is not None and int(region_page) != pno:
                raise ValueError(
                    f"formula cache {path}: region physical_page {region_page!r} "
                    f"does not match requested page {pno}")
            latex = region.get("raw_latex")
            if latex is not None and not isinstance(latex, str):
                raise ValueError(
                    f"formula cache {path}: raw_latex must be a string or null, "
                    f"got {type(latex).__name__}")
            region = dict(region)
            region["_formula_run"] = run_meta
            regions.append(region)
        out[pno] = regions
    return out, failures


# --------------------------------------------------------------------------- #
# T04: formula region identity, cache/detection reconciliation, outcome ledger
# --------------------------------------------------------------------------- #

# Formula recognition has exactly one repo-defined route: the formula engine
# runs on the (cropped) region image. Kind-scoped in the domain validator.
_FORMULA_ROUTE = "formula_crop_ocr"
# Max page-points coordinate delta for matching a cache region to its detected
# source region. Real-corpus drift is <= 0.0001pt; distinct regions are far
# wider apart, so 0.5pt cannot cross-match (and any ambiguity fails loudly).
_FORMULA_MATCH_TOLERANCE = 0.5
_FORMULA_QUALITY = "needs_review"  # D1: never upgraded without a review id


# --------------------------------------------------------------------------- #
# T05: figure region identity, asset verification, outcome ledger
# --------------------------------------------------------------------------- #

# Figure/image processing has exactly one repo-defined route: the region crop
# step (the registered image-1 "image-crop" engine). Kind-scoped in the domain
# validator, like the formula route above.
_FIGURE_ROUTE = "image_crop"
_FIGURE_QUALITY = "needs_review"  # D1: never upgraded without a review id
# Media types claimable from the crop file extension -- anything else stays
# unreadable (an explicit failure), never guessed.
_FIGURE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}


def _figure_identity(
    sha: str, pno: int, bbox: list[float], config_token: str | None,
) -> str:
    """Deterministic stable region identity: ``fg-<24 hex>``.

    Same derivation contract as the formula ``fx-`` identity: file sha +
    physical page + the (2dp-rounded) bbox + the processing config token --
    never the array position. This is the figure recognition-RESULT identity
    (and the fallback SOURCE identity only when the page-intermediate carries
    no ``region_id``; a real upstream region_id is preserved verbatim).
    """
    payload = json.dumps(
        [sha, int(pno), [round(float(v), 2) for v in bbox],
         str(config_token or "")],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fg-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _image_pixel_size(path: str) -> tuple[int, int] | None:
    """True pixel size read from the image header -- PNG IHDR or JPEG SOFn.

    Returns ``None`` when the file is not a readable PNG/JPEG: the caller then
    records an explicit ``crop_asset_unreadable`` issue instead of guessing
    pixel dimensions (T05: no pt->px heuristics).
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    if head[:3] == b"\xff\xd8\xff":  # JPEG: walk segment markers to the SOFn
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    return None
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if i + 4 > len(data):
                    return None
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                if (0xC0 <= marker <= 0xCF
                        and marker not in (0xC4, 0xC8, 0xCC)):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return (int(w), int(h)) if w > 0 and h > 0 else None
                if seg_len <= 0:
                    return None
                i += 2 + seg_len
        except (OSError, ValueError, IndexError):
            return None
    return None


def _figure_verified_asset(
    region: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    """Verify the page-intermediate's recorded crop asset (T05).

    Only the PI's real ``image_ref`` / ``image_sha256`` is used, and the file
    itself is verified: exists, digest matches the recorded sha256, true pixel
    size readable from the image header. Returns
    ``(content_asset, issue, crop_assets_audit)``:

    * ``content_asset`` -- the closed FigureAsset dict (real ref/sha/media/
      pixel dims) when everything verifies, else ``None``;
    * ``issue`` -- the explicit failure reason (``missing_crop_asset`` /
      ``crop_asset_file_missing`` / ``crop_asset_sha_mismatch`` /
      ``crop_asset_unreadable``) or None;
    * ``crop_assets_audit`` -- the recorded {ref, sha256} provenance whenever
      the upstream recorded both, regardless of file verification (audit truth
      is what upstream recorded; execution status is decided separately).
    """
    ref = region.get("image_ref")
    sha = region.get("image_sha256")
    if not (isinstance(ref, str) and ref.strip()):
        return None, "missing_crop_asset", []
    if not (isinstance(sha, str) and sha.strip()):
        return None, "missing_crop_asset", []
    audit = [{"ref": ref, "sha256": sha}]
    if not os.path.isfile(ref):
        return None, "crop_asset_file_missing", audit
    try:
        with open(ref, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None, "crop_asset_file_missing", audit
    if digest != sha.lower():
        return None, "crop_asset_sha_mismatch", audit
    dims = _image_pixel_size(ref)
    media = _FIGURE_MEDIA_TYPES.get(os.path.splitext(ref)[1].lower())
    if dims is None or media is None:
        return None, "crop_asset_unreadable", audit
    content = {
        "ref": ref,
        "sha256": sha.lower(),
        "media_type": media,
        "pixel_width": dims[0],
        "pixel_height": dims[1],
    }
    return content, None, audit


def _figure_issues(detected: dict[str, Any]) -> list[str]:
    """Base outcome issues: the detected region's issues verbatim, plus the
    detection execution status when it was not completed (T05: faithful
    recording -- no heuristic caption/reference issue strings are invented
    here; the structured relation records carry the associations losslessly)."""
    issues: list[str] = []
    for iss in detected.get("issues") or []:
        if iss not in issues:
            issues.append(iss)
    exec_status = detected.get("execution_status")
    if exec_status is not None and exec_status != "completed":
        issue = f"detection_execution_status:{exec_status}"
        if issue not in issues:
            issues.append(issue)
    return issues


def _figure_crop_attempt(
    region: dict[str, Any], *, identity: str, completed: bool,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """One attempt record for the region's crop step (route ``image_crop``).

    Faithful to the page-intermediate: the attempt's input AND output identity
    is the REAL recorded crop asset (``asset-sha256:<hex>``) -- a bbox is never
    claimed as a crop identity and no OCR/description result is invented.
    ``confidence`` stays None (the upstream records no score). Engine identity
    is the repo-defined cropping step (registered as image-1 "image-crop").
    """
    crop_assets = region.get("_figure_crop_assets") or []
    asset_sha = crop_assets[0].get("sha256") if crop_assets else None
    asset_identity = (
        f"asset-sha256:{asset_sha}"
        if isinstance(asset_sha, str) and asset_sha else None)
    raw = {
        "route": _FIGURE_ROUTE,
        "execution_status": "completed" if completed else "failed",
        "engine_name": "image-crop",
        "engine_version": "1.0",
        "config_id": region.get("processing_config_id"),
        "config_hash": None,
        "input_identity": asset_identity,
        "output_identity": asset_identity if completed else None,
        "confidence": None,
        "failure_reason": failure_reason,
    }
    return _normalize_attempt(raw, result_id=identity, input_identity=None)


def _figure_outcome(
    region_id: str, pno: int, bbox: list[float] | None, *,
    execution_status: str, issues: list[str], attempts: list[dict[str, Any]],
    processing_config_id: str | None, admissible_result_ids: list[str],
    detection_config_id: str | None = None,
    recognition_config_id: str | None = None,
    crop_assets: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    element_id: str | None = None, selection_basis: str | None = None,
    selected_result_id: str | None = None, selected_attempt_id: str | None = None,
) -> dict[str, Any]:
    """Closed outcome-ledger entry for one detected figure region (same closed
    schema as the T03/T04 outcomes, kind="figure"), with the full
    lossless-provenance keys on completed, failed AND unsupported outcomes
    alike, so every outcome is self-sufficient for full source traceback even
    without an element."""
    return {
        "region_id": region_id,
        "kind": "figure",
        "physical_page": pno,
        "bbox": list(bbox) if bbox else None,
        "execution_status": execution_status,
        "quality_status": _FIGURE_QUALITY,
        "issues": list(issues),
        "processing_config_id": processing_config_id,
        "detection_config_id": detection_config_id,
        "recognition_config_id": recognition_config_id,
        "crop_assets": [dict(c) for c in (crop_assets or [])],
        "cross_page_relations": [dict(r) for r in (relations or [])],
        "element_id": element_id,
        "selection_basis": selection_basis,
        "admissible_result_ids": sorted(admissible_result_ids),
        "selected_result_id": selected_result_id,
        "selected_attempt_id": selected_attempt_id,
        "attempts": _stabilise_attempt_ids(attempts),
    }


def _reconcile_figure_page(
    page_row: dict[str, Any], pno: int, sha: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Asset reconciliation for one page's detected image regions (T05).

    Returns ``(outcomes, consumable_regions, pending_outcomes)``:

    * outcomes -- one outcome per DETECTED image region on the page, whatever
      happened to it (completed / failed / unsupported; never silently
      skipped, never ``not_detected``);
    * consumable_regions -- detected regions whose REAL crop asset verified
      (file exists, digest matches, true pixel size readable) and whose
      element still has to be built (annotated with the pending outcome and
      the stable identity);
    * pending_outcomes -- outcomes awaiting the element pass; a pending
      outcome whose element is never built (e.g. the page was not parsed)
      closes as an explicit failure, never as silence.

    The execution contract: ``completed`` means ONLY "the real crop asset was
    faithfully assembled" -- never that the image content was understood
    (quality stays needs_review, and no OCR/description text is fabricated).
    """
    detected = [r for r in (page_row.get("regions") or [])
                if r.get("kind") == "image"]
    outcomes: list[dict[str, Any]] = []
    consumable: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for d in detected:
        d = dict(d)
        issues = _figure_issues(d)
        detection_cfg = d.get("processing_config_id")
        if not (isinstance(detection_cfg, str) and detection_cfg):
            detection_cfg = None
        d["processing_config_id"] = detection_cfg
        # Outcome-side relation records are built with no span projection
        # (spans belong to elements); a completed outcome is later synced to
        # its element so the shared provenance is identical on both sides.
        relations, _rel_spans = _formula_relation_records(
            d, _Counter("s"), set(), {}, id_prefix="irel-")
        bbox = _coerce_bbox(d.get("bbox"))
        if bbox is None:
            # Detected but with unusable coordinates: the region cannot be
            # cropped or located -- an explicit unsupported close. The recorded
            # crop provenance and relations are preserved verbatim.
            fallback = _figure_identity(
                sha, pno, [0.0, 0.0, 0.0, 0.0],
                f"invalid-coords:{d.get('region_id')}")
            asset_sha = d.get("image_sha256")
            audit = ([{"ref": d.get("image_ref"), "sha256": asset_sha}]
                     if isinstance(asset_sha, str) and asset_sha else [])
            d["_figure_crop_assets"] = audit
            attempts = [_figure_crop_attempt(
                d, identity=fallback, completed=False,
                failure_reason="invalid_detection_coordinates")]
            if "invalid_detection_coordinates" not in issues:
                issues.append("invalid_detection_coordinates")
            outcomes.append(_figure_outcome(
                _formula_source_region_id(d, fallback), pno, None,
                execution_status="unsupported",
                issues=issues, attempts=attempts,
                processing_config_id=detection_cfg,
                detection_config_id=detection_cfg,
                crop_assets=audit, relations=relations,
                admissible_result_ids=[]))
            continue
        fallback = _figure_identity(sha, pno, bbox, detection_cfg)
        asset, asset_issue, audit = _figure_verified_asset(d)
        d["_figure_asset"] = asset
        d["_figure_crop_assets"] = audit
        if asset_issue is not None:
            # The recorded crop did not verify (missing / file absent / digest
            # mismatch / unreadable header): an explicit failed close -- the
            # element cannot faithfully claim a real asset.
            if asset_issue not in issues:
                issues.append(asset_issue)
            attempts = [_figure_crop_attempt(
                d, identity=fallback, completed=False,
                failure_reason=asset_issue)]
            outcomes.append(_figure_outcome(
                _formula_source_region_id(d, fallback), pno, bbox,
                execution_status="failed", issues=issues, attempts=attempts,
                processing_config_id=detection_cfg,
                detection_config_id=detection_cfg,
                recognition_config_id=detection_cfg,
                crop_assets=audit, relations=relations,
                admissible_result_ids=[]))
            continue
        # Asset verified: the element pass closes this outcome (completed) --
        # until then it stays an explicit pending failure.
        attempts = [_figure_crop_attempt(d, identity=fallback, completed=True)]
        outcome = _figure_outcome(
            _formula_source_region_id(d, fallback), pno, bbox,
            execution_status="failed", issues=issues, attempts=attempts,
            processing_config_id=detection_cfg,
            detection_config_id=detection_cfg,
            recognition_config_id=detection_cfg,
            crop_assets=audit, relations=relations,
            admissible_result_ids=[fallback])
        pending.append(outcome)
        d["_figure_outcome"] = outcome
        d["_figure_identity"] = fallback
        d["_figure_source_id"] = outcome["region_id"]
        consumable.append(d)
    return outcomes, consumable, pending


def _formula_identity(
    sha: str, pno: int, bbox: list[float], config_token: str | None,
) -> str:
    """Deterministic stable region identity: ``fx-<24 hex>``.

    Derived ONLY from file sha + physical page + the (2dp-rounded) bbox + the
    processing config token -- NEVER from the array position or the upstream
    ``candidate_id`` (which encodes the region index and therefore breaks when
    the detection order changes). A cache/config/bbox change therefore flips
    the identity (and with it the canonical_id).

    T04-R2: this is the *recognition result* identity (and the fallback SOURCE
    identity only when the page-intermediate carries no ``region_id``). A real
    upstream region_id is preserved verbatim -- see
    :func:`_formula_source_region_id`.
    """
    payload = json.dumps(
        [sha, int(pno), [round(float(v), 2) for v in bbox],
         str(config_token or "")],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fx-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _formula_source_region_id(
    detected: dict[str, Any], fallback: str,
) -> str:
    """The SOURCE region identity of one detected formula region (T04-R2).

    The page-intermediate's ``region_id`` (e.g. ``up-...``) is the real source
    identity and is preserved VERBATIM. The ``fallback`` (a deterministic
    ``fx-`` derivation from sha/page/bbox/config) is used ONLY when the
    historical record carries no region_id -- never the array position or the
    upstream ``candidate_id``.
    """
    rid = detected.get("region_id")
    if isinstance(rid, str) and rid.strip():
        return rid
    return fallback


def _formula_relation_side(block: Any) -> dict[str, Any] | None:
    """Normalise one side (source/target) of an upstream relation, verbatim.

    Preserves the region identity, page, bbox, formula number and the
    parameter/condition text exactly as the page-intermediate recorded them;
    absent fields stay ``None`` -- nothing is guessed or interpolated.
    """
    if not isinstance(block, dict):
        return None
    bbox = block.get("bbox")
    if not _valid_bbox4(bbox):
        bbox = None
    else:
        bbox = [float(v) for v in bbox]
    page = block.get("physical_page")
    page = int(page) if isinstance(page, int) and not isinstance(page, bool) else None
    return {
        "region_id": block.get("region_id"),
        "element_id": block.get("element_id"),
        "physical_page": page,
        "bbox": bbox,
        "text": block.get("text"),
        "formula_number": block.get("formula_number"),
    }


def _formula_relation_records(
    detected: dict[str, Any],
    span_counter: "_Counter",
    parsed_pages: set[int],
    page_dims: dict[int, tuple[float, float]],
    *,
    id_prefix: str = "frel-",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Structured cross-page parameter/condition relations (T04-R2).

    Projects every upstream relation of the detected region into a closed,
    auditable record: kind / basis / verified / association_status verbatim,
    both side blocks lossless (:func:`_formula_relation_side`), a deterministic
    ``relation_id`` derived from the relation content, and -- when the relation
    source carries a usable page+bbox on a PARSED page -- a ``context`` source
    span on the element so the parameter/condition text is traceable to its
    page location. An unresolvable or absent target is recorded as-is (no
    adjacency guessing, no fabricated link); the element stays needs_review.

    T05 reuses this generic projection for figure relations (caption binding +
    explicit body figure references) with the ``irel-`` id prefix.

    Returns ``(records, extra_spans)`` -- the caller appends the spans to the
    element's ``source_spans`` and the records to ``cross_page_relations``.
    """
    records: list[dict[str, Any]] = []
    extra_spans: list[dict[str, Any]] = []
    for rel in detected.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        kind = str(rel.get("kind") or "")
        if not kind:
            continue
        source = _formula_relation_side(rel.get("source"))
        target = _formula_relation_side(rel.get("target"))
        span_id: str | None = None
        if (source is not None and source["physical_page"] is not None
                and source["bbox"] is not None
                and source["physical_page"] in parsed_pages):
            w, h = page_dims.get(source["physical_page"], (0.0, 0.0))
            bx = source["bbox"]
            if (w > 0 and h > 0 and 0 <= bx[0] <= bx[2] <= w
                    and 0 <= bx[1] <= bx[3] <= h):
                span = _make_span(span_counter, "context",
                                  source["physical_page"], bx)
                span_id = span["span_id"]
                extra_spans.append(span)
        verified = rel.get("verified")
        verified = bool(verified) if isinstance(verified, bool) else None
        record = {
            "relation_id": id_prefix + hashlib.sha256(json.dumps(
                rel, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()[:12],
            "kind": kind,
            "basis": rel.get("basis"),
            "verified": verified,
            "association_status": rel.get("association_status"),
            "source": source,
            "target": target,
            "source_span_id": span_id,
            "target_element_id": None,  # resolved in the phase-2 pass
        }
        records.append(record)
    return records, extra_spans


def _valid_bbox4(bbox: Any) -> bool:
    return (isinstance(bbox, list) and len(bbox) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in bbox))


def _formula_bbox_close(a: list[float], b: list[float]) -> bool:
    """True when two bboxes agree within ``_FORMULA_MATCH_TOLERANCE``."""
    return all(abs(float(x) - float(y)) <= _FORMULA_MATCH_TOLERANCE
               for x, y in zip(a, b))


def _match_formula_cache(
    detected: list[dict[str, Any]], cache_regions: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Pair every detected region with its unique cache region (or None).

    Order-independent: the pairing is derived from bbox geometry only, never
    from list positions. Hard-fails (``ValueError``) on any disagreement
    between detection and cache -- "one extra / one ambiguous" must never pass
    silently (T04 reconciliation):

    * a cache region with no detected counterpart -> error (the cache claims a
      region the detector never detected);
    * a cache region matching multiple detected regions, or two cache regions
      matching the same detected region -> error (duplicate/ambiguous).
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    claimed_detected: set[int] = set()
    used_cache: set[int] = set()
    for c_idx, c_region in enumerate(cache_regions):
        cb = c_region.get("bbox")
        candidates = [
            (d_idx, d) for d_idx, d in enumerate(detected)
            if _valid_bbox4(d.get("bbox")) and _formula_bbox_close(d["bbox"], cb)
        ]
        if not candidates:
            raise ValueError(
                "formula cache region without a detected source region: "
                f"bbox={cb!r} (detection and formula cache disagree)")
        if len(candidates) > 1:
            raise ValueError(
                "formula cache region matches multiple detected regions "
                f"(ambiguous): bbox={cb!r}, "
                f"detected={[d.get('region_id') for _i, d in candidates]!r}")
        d_idx, d = candidates[0]
        if d_idx in claimed_detected:
            raise ValueError(
                "duplicate formula cache regions claim the same detected "
                f"region {d.get('region_id')!r}")
        claimed_detected.add(d_idx)
        used_cache.add(c_idx)
        pairs.append((d, c_region))
    for d_idx, d in enumerate(detected):
        if d_idx not in claimed_detected:
            pairs.append((d, None))
    return pairs


def _formula_gate(bbox: list[float], score: Any) -> tuple[bool, str | None]:
    """Element-admission gate for a recognised formula region.

    Same thresholds the element path always used (``_FORMULA_MIN_SCORE`` /
    ``_FORMULA_MIN_AREA``), but the decision is now RECORDED: a rejected region
    closes as an ``unsupported`` outcome instead of being silently dropped.
    """
    try:
        score_f = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score_f = 0.0
    area = (float(bbox[2]) - float(bbox[0])) * (float(bbox[3]) - float(bbox[1]))
    if score_f < _FORMULA_MIN_SCORE:
        return False, (f"formula_below_gate_threshold:score={score_f:.4f}"
                       f"<{_FORMULA_MIN_SCORE}")
    if area < _FORMULA_MIN_AREA:
        return False, (f"formula_below_gate_threshold:area={area:.1f}"
                       f"<{_FORMULA_MIN_AREA}")
    return True, None


def _formula_issues(detected: dict[str, Any]) -> list[str]:
    """Base outcome issues: the detected region's issues verbatim, the
    detection execution status when it was not completed, and one explicit
    issue per cross-page parameter/condition relation (T04: cross-page
    knowledge enters ONLY through the upstream's explicit source relations;
    unknown/uncertain relations are recorded, never promoted)."""
    issues: list[str] = []
    for iss in detected.get("issues") or []:
        if iss not in issues:
            issues.append(iss)
    exec_status = detected.get("execution_status")
    if exec_status is not None and exec_status != "completed":
        issue = f"detection_execution_status:{exec_status}"
        if issue not in issues:
            issues.append(issue)
    for rel in detected.get("relations") or []:
        kind = str(rel.get("kind") or "")
        if not kind:
            continue
        src = rel.get("source") or {}
        page_from = src.get("physical_page")
        if kind.startswith("explicit"):
            issue = f"cross_page_parameter_relation_explicit:{kind}"
        else:
            issue = f"cross_page_parameter_relation_uncertain:{kind}"
        if page_from is not None:
            issue += f":from_page={page_from}"
        if issue not in issues:
            issues.append(issue)
    return issues


def _formula_recognition_attempt(
    cache_region: dict[str, Any], *, identity: str, completed: bool,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """One attempt record for the region's recognition result.

    Faithful to the cache: engine/config from the producing run's provenance,
    confidence from the region score, output identity from the sha256 of the
    recognized latex (so a latex change flips the attempt -- and therefore the
    canonical_id). ``input_identity`` binds the REAL crop asset the
    recognition consumed (``asset-sha256:<hex>`` from the page-intermediate's
    recorded crop) -- a bbox is never claimed as a crop identity (T04-R2);
    when no asset exists the caller records an explicit
    ``missing_crop_asset`` issue instead. The attempt is bound to the stable
    region identity via ``result_id``; ``selected`` stays False until the
    caller marks exactly one completed attempt on a completed outcome.
    """
    run_meta = cache_region.get("_formula_run") or {}
    model = str(run_meta.get("formula_model") or "")
    # formula_model is written as "<namespace>:<model-name>"; keep the model
    # name and never guess beyond what the cache records.
    engine_name = model.rsplit(":", 1)[-1] if model else None
    latex = cache_region.get("raw_latex") or ""
    asset = cache_region.get("_formula_asset") or {}
    asset_sha = asset.get("sha256")
    input_identity = (
        f"asset-sha256:{asset_sha}" if isinstance(asset_sha, str) and asset_sha
        else None)
    raw = {
        "route": _FORMULA_ROUTE,
        "execution_status": "completed" if completed else "failed",
        "engine_name": engine_name,
        "engine_version": run_meta.get("paddleocr_version"),
        "config_id": run_meta.get("profile"),
        "config_hash": run_meta.get("config_hash"),
        "input_identity": input_identity,
        "output_identity": (
            "latex-sha256:" + hashlib.sha256(latex.encode("utf-8")).hexdigest()
            if completed else None),
        "confidence": cache_region.get("score"),
        "failure_reason": failure_reason,
    }
    attempt = _normalize_attempt(raw, result_id=identity, input_identity=None)
    if attempt.get("confidence") is not None:
        try:
            attempt["confidence"] = round(float(attempt["confidence"]), 6)
        except (TypeError, ValueError):
            attempt["confidence"] = None
    return attempt


def _formula_outcome(
    region_id: str, pno: int, bbox: list[float] | None, *,
    execution_status: str, issues: list[str], attempts: list[dict[str, Any]],
    processing_config_id: str | None, admissible_result_ids: list[str],
    detection_config_id: str | None = None,
    recognition_config_id: str | None = None,
    crop_assets: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    element_id: str | None = None, selection_basis: str | None = None,
    selected_result_id: str | None = None, selected_attempt_id: str | None = None,
) -> dict[str, Any]:
    """Closed outcome-ledger entry for one detected formula region (same
    closed schema as the T03 table outcome, kind="formula"), extended with the
    T04-R3 lossless-provenance keys: BOTH configs (detection + recognition),
    the real crop asset(s) and the structured cross-page relations -- on
    completed, failed AND unsupported outcomes alike, so every outcome is
    self-sufficient for full source traceback even without an element."""
    return {
        "region_id": region_id,
        "kind": "formula",
        "physical_page": pno,
        "bbox": list(bbox) if bbox else None,
        "execution_status": execution_status,
        "quality_status": _FORMULA_QUALITY,
        "issues": list(issues),
        "processing_config_id": processing_config_id,
        "detection_config_id": detection_config_id,
        "recognition_config_id": recognition_config_id,
        "crop_assets": [dict(c) for c in (crop_assets or [])],
        "cross_page_relations": [dict(r) for r in (relations or [])],
        "element_id": element_id,
        "selection_basis": selection_basis,
        "admissible_result_ids": sorted(admissible_result_ids),
        "selected_result_id": selected_result_id,
        "selected_attempt_id": selected_attempt_id,
        "attempts": _stabilise_attempt_ids(attempts),
    }


def _reconcile_formula_page(
    page_row: dict[str, Any], pno: int, sha: str,
    cache_regions: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Three-way reconciliation for one page (T04).

    Returns ``(outcomes, consumable_regions, pending_outcomes)``:

    * outcomes -- one outcome per DETECTED formula region on the page,
      whatever happened to it (completed / failed / unsupported; never
      ``not_detected``, never silently skipped);
    * consumable_regions -- cache regions that passed the element gate and
      whose element still has to be built (annotated with the pending outcome
      and stable identity);
    * pending_outcomes -- outcomes awaiting the element pass; a pending outcome
      whose element is never built (e.g. the page was not parsed) closes as an
      explicit failure, never as silence.
    """
    detected = [r for r in (page_row.get("regions") or [])
                if r.get("kind") == "formula"]
    outcomes: list[dict[str, Any]] = []
    consumable: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    if not detected:
        # No detected regions on this page -- a non-empty cache list would be
        # an "extra" disagreement; surface it via the matcher below.
        if cache_regions:
            _match_formula_cache(detected, cache_regions)  # raises ValueError
        return outcomes, consumable, pending
    pairs = _match_formula_cache(detected, cache_regions or [])
    for d, c in pairs:
        issues = _formula_issues(d)
        # T04-R3: the lossless provenance EVERY outcome carries, whatever its
        # execution status -- the REAL crop asset recorded on the detected
        # region (or an explicit ``missing_crop_asset`` issue, never a bbox
        # posing as a crop identity) and the structured cross-page relations.
        # Outcome provenance is built with no span projection (spans belong to
        # elements); a completed outcome is later synced to its element so the
        # shared provenance is identical on both sides.
        asset_sha = d.get("image_sha256")
        asset = ({"ref": d.get("image_ref"), "sha256": asset_sha}
                 if isinstance(asset_sha, str) and asset_sha else None)
        if asset is None and "missing_crop_asset" not in issues:
            issues.append("missing_crop_asset")
        detection_cfg = d.get("processing_config_id")
        if not (isinstance(detection_cfg, str) and detection_cfg):
            detection_cfg = None
        relations, _rel_spans = _formula_relation_records(
            d, _Counter("s"), set(), {})
        crop_assets = [asset] if asset else []
        bbox = d.get("bbox") if _valid_bbox4(d.get("bbox")) else None
        if bbox is None:
            # Detected but with unusable coordinates: the region cannot be
            # cropped, recognised or located -- an explicit unsupported close.
            fallback = _formula_identity(
                sha, pno, [0.0, 0.0, 0.0, 0.0],
                f"invalid-coords:{d.get('region_id')}")
            attempts = [_synthesize_attempt(
                fallback, None, completed=False, route=_FORMULA_ROUTE,
                failure_reason="invalid_detection_coordinates",
                config_id=detection_cfg)]
            if "invalid_detection_coordinates" not in issues:
                issues.append("invalid_detection_coordinates")
            outcomes.append(_formula_outcome(
                _formula_source_region_id(d, fallback), pno, None,
                execution_status="unsupported",
                issues=issues, attempts=attempts,
                processing_config_id=detection_cfg,
                detection_config_id=detection_cfg,
                crop_assets=crop_assets, relations=relations,
                admissible_result_ids=[]))
            continue
        if c is None:
            # Detected but the cache carries no result for it (missing page
            # file or missing region): explicit failure, never a silent drop.
            fallback = _formula_identity(
                sha, pno, bbox, d.get("processing_config_id"))
            attempts = [_synthesize_attempt(
                fallback, None, completed=False, route=_FORMULA_ROUTE,
                failure_reason="formula_cache_result_missing",
                config_id=detection_cfg)]
            if "formula_cache_result_missing" not in issues:
                issues.append("formula_cache_result_missing")
            outcomes.append(_formula_outcome(
                _formula_source_region_id(d, fallback), pno, bbox,
                execution_status="failed", issues=issues,
                attempts=attempts,
                processing_config_id=detection_cfg,
                detection_config_id=detection_cfg,
                crop_assets=crop_assets, relations=relations,
                admissible_result_ids=[]))
            continue
        run_meta = c.get("_formula_run") or {}
        config_hash = run_meta.get("config_hash")
        fallback = _formula_identity(sha, pno, bbox, config_hash)
        c = dict(c)
        c["_formula_asset"] = asset
        latex = (c.get("raw_latex") or "").strip()
        if not latex:
            attempt = _formula_recognition_attempt(
                c, identity=fallback, completed=False,
                failure_reason="empty_formula_recognition")
            if "empty_formula_recognition" not in issues:
                issues.append("empty_formula_recognition")
            outcomes.append(_formula_outcome(
                _formula_source_region_id(d, fallback), pno, bbox,
                execution_status="failed", issues=issues,
                attempts=[attempt], processing_config_id=run_meta.get("profile"),
                detection_config_id=detection_cfg,
                recognition_config_id=run_meta.get("profile"),
                crop_assets=crop_assets, relations=relations,
                admissible_result_ids=[]))
            continue
        ok, reason = _formula_gate(bbox, c.get("score"))
        attempt = _formula_recognition_attempt(
            c, identity=fallback, completed=True)
        if not ok:
            # Recognition succeeded, but the region is below the element
            # admission gate: recorded as unsupported -- the recognition is
            # preserved in the ledger, nothing is silently dropped.
            if reason not in issues:
                issues.append(reason)
            outcomes.append(_formula_outcome(
                _formula_source_region_id(d, fallback), pno, bbox,
                execution_status="unsupported",
                issues=issues, attempts=[attempt],
                processing_config_id=run_meta.get("profile"),
                detection_config_id=detection_cfg,
                recognition_config_id=run_meta.get("profile"),
                crop_assets=crop_assets, relations=relations,
                admissible_result_ids=[fallback]))
            continue
        # Gate-passing: the element pass closes this outcome (completed) --
        # until then it stays an explicit pending failure.
        outcome = _formula_outcome(
            _formula_source_region_id(d, fallback), pno, bbox,
            execution_status="failed", issues=issues,
            attempts=[attempt], processing_config_id=run_meta.get("profile"),
            detection_config_id=detection_cfg,
            recognition_config_id=run_meta.get("profile"),
            crop_assets=crop_assets, relations=relations,
            admissible_result_ids=[fallback])
        pending.append(outcome)
        c["_formula_outcome"] = outcome
        c["_formula_identity"] = fallback
        c["_formula_source_id"] = outcome["region_id"]
        c["_formula_detected"] = d
        consumable.append(c)
    return outcomes, consumable, pending


# --------------------------------------------------------------------------- #
# Page-item collection
# --------------------------------------------------------------------------- #


def _collect_page_items(
    page_row: dict[str, Any],
    pno: int,
    width: float,
    height: float,
    formula_regions: list[dict[str, Any]] | None,
    *,
    figure_regions: list[dict[str, Any]] | None = None,
    route: str | None = None,
    decision: str | None = None,
    decision_reasons: list[str] | None = None,
    ocr_confidence: float | None = None,
    engine_name: str = "",
    engine_version: str = "",
    page_engine_id: str | None = None,
) -> list[_PageItem]:
    """Gather text / formula / table / figure items for a single page."""
    items: list[_PageItem] = []
    reasons = list(decision_reasons or [])

    # T02 P1a: index the page's upstream text regions (kind=text) so each text
    # element can be matched to its source region(s) and read each region's real
    # quality_status / issues / region_id / processing_config_id instead of
    # re-deriving quality. Structured regions (table/image) are handled below.
    text_regions = [
        r for r in (page_row.get("regions") or []) if r.get("kind") == "text"
    ]
    text_region_bboxes = []
    for r in text_regions:
        rb = _coerce_bbox(r.get("bbox"))
        if rb is not None:
            text_region_bboxes.append((rb, r))

    for elem in page_row.get("elements") or []:
        bbox = _coerce_bbox(elem.get("bbox"))
        if bbox is None:
            continue
        text = (elem.get("text") or "")
        matched = _match_text_regions(bbox, text_region_bboxes)
        # Merge ALL participating regions losslessly (R6 one-to-many).
        region_ids = [m.get("region_id") for m in matched if m.get("region_id")]
        region_qualities = [m.get("quality_status") for m in matched if m.get("quality_status")]
        region_issues: list[str] = []
        region_cfgs: list[str] = []
        for m in matched:
            for iss in (m.get("issues") or []):
                if iss not in region_issues:
                    region_issues.append(iss)
            cfg = m.get("processing_config_id")
            if cfg and cfg not in region_cfgs:
                region_cfgs.append(cfg)
        v3_source_region_id = elem.get("_v3_region_id")
        if v3_source_region_id:
            matched = [m for m in text_regions if m.get("region_id") == v3_source_region_id]
            region_ids = [v3_source_region_id]
            region_qualities = [m.get("quality_status") for m in matched if m.get("quality_status")]
            region_issues = [issue for m in matched for issue in (m.get("issues") or [])]
            region_cfgs = [m.get("processing_config_id") for m in matched if m.get("processing_config_id")]
        extra: dict[str, Any] = {"ocr_fusion": elem.get("_ocr_fusion")}
        if elem.get("_v3_block"):
            extra.update({
                "ocr_fusions": list(elem.get("_v3_ocr_fusions") or []),
                "ocr_text_sources": list(elem.get("_v3_ocr_text_sources") or []),
                "source_line_bboxes": list(elem.get("_v3_source_line_bboxes") or []),
                "v3_block": True,
                "v3_role": elem.get("_v3_role"),
                "v3_model_label": elem.get("_v3_model_label"),
                "v3_heading": elem.get("_v3_heading"),
                "v3_furniture_rule": elem.get("_v3_furniture_rule"),
            })
        items.append(_PageItem(
            kind="text", page=pno, bbox=bbox, text=text, ref=elem,
            extra=extra,
            extraction_route=route, decision_status=decision, decision_reasons=reasons,
            ocr_confidence=ocr_confidence, engine_name=engine_name, engine_version=engine_version,
            engine_id=page_engine_id,
            source_region_ids=region_ids,
            region_qualities=region_qualities,
            region_issues=region_issues,
            region_processing_config_ids=region_cfgs,
            has_text_regions=bool(text_regions),
        ))

    # Formula regions from the per-page formula cache. The caller passes ONLY
    # regions that survived the T04 reconciliation (matched to a detected
    # region + latex present + above the element gate) -- every other region
    # closed as an explicit outcome there, so nothing is silently dropped.
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
            continue  # recon guarantees a valid bbox; defensive only
        latex = region.get("raw_latex") or ""
        items.append(
            _PageItem(
                kind="formula",
                page=pno,
                bbox=bbox,
                text=latex,
                ref=region,
                extra={"score": score_f, "latex": latex},
                extraction_route=route, decision_status=decision, decision_reasons=reasons,
                ocr_confidence=ocr_confidence, engine_name=engine_name, engine_version=engine_version,
                engine_id=page_engine_id or "native-1",
            )
        )

    # Table regions from the V1 page `regions` list (page-points bbox).
    for region in page_row.get("regions") or []:
        if region.get("kind") != "table":
            continue
        bbox = _coerce_bbox(region.get("bbox"))
        if bbox is None:
            continue
        items.append(
            _PageItem(
                kind="table",
                page=pno,
                bbox=bbox,
                ref=region,
                extra={"reading_order": region.get("reading_order")},
                extraction_route=route, decision_status=decision, decision_reasons=reasons,
                ocr_confidence=ocr_confidence, engine_name=engine_name, engine_version=engine_version,
                engine_id=page_engine_id or "native-1",
            )
        )

    # Figure regions from the T05 asset reconciliation. The caller passes ONLY
    # detected image regions whose REAL crop asset verified -- every other
    # detected region closed as an explicit outcome there (invalid bbox ->
    # unsupported; missing/unverifiable asset -> failed), so nothing is
    # silently dropped.
    for region in figure_regions or []:
        if region.get("kind") != "image":
            continue
        bbox = _coerce_bbox(region.get("bbox"))
        if bbox is None:
            continue  # recon guarantees a valid bbox; defensive only
        items.append(
            _PageItem(
                kind="figure",
                page=pno,
                bbox=bbox,
                ref=region,
                extra={"reading_order": region.get("reading_order")},
                extraction_route=route, decision_status=decision, decision_reasons=reasons,
                ocr_confidence=ocr_confidence, engine_name=engine_name, engine_version=engine_version,
                engine_id=page_engine_id or "native-1",
            )
        )

    return items


def _reconstruct_ocr_paragraphs(items: list[_PageItem]) -> list[_PageItem]:
    """Merge OCR line boxes into paragraph/clause elements on one page.

    The function runs only after reading-order resolution and only for OCR
    routes.  It never changes line text: joined lines are separated by a
    literal newline, and every source bbox is retained for later span emission.
    Structured elements break a text flow and are never merged.
    """
    out: list[_PageItem] = []
    pending: _PageItem | None = None

    def seal() -> None:
        nonlocal pending
        if pending is not None:
            out.append(pending)
            pending = None

    for item in items:
        if item.kind != "text" or not (item.text or "").strip():
            seal()
            out.append(item)
            continue
        if pending is None:
            pending = item
            pending.extra.setdefault("source_line_bboxes", [list(item.bbox)])
            continue
        if not _ocr_lines_share_flow(pending, item):
            seal()
            pending = item
            pending.extra.setdefault("source_line_bboxes", [list(item.bbox)])
            continue
        previous = (pending.text or "").rstrip()
        current = (item.text or "").strip()
        if (_OCR_SENTENCE_END_RE.search(previous)
                or _is_ocr_paragraph_start(current)):
            seal()
            pending = item
            pending.extra.setdefault("source_line_bboxes", [list(item.bbox)])
            continue
        source_line_bboxes = pending.extra.setdefault(
            "source_line_bboxes", [list(pending.bbox)]
        )
        pending.text = pending.text + "\n" + item.text
        pending.bbox = [
            min(pending.bbox[0], item.bbox[0]),
            min(pending.bbox[1], item.bbox[1]),
            max(pending.bbox[2], item.bbox[2]),
            max(pending.bbox[3], item.bbox[3]),
        ]
        source_line_bboxes.append(list(item.bbox))
        pending.extra["paragraph_reconstruction"] = _OCR_PARAGRAPH_RULE_VERSION
        fusions = pending.extra.setdefault("ocr_fusions", [])
        for fusion in (pending.extra.get("ocr_fusion"), item.extra.get("ocr_fusion")):
            if fusion and fusion not in fusions:
                fusions.append(fusion)
        for value in item.source_region_ids:
            if value not in pending.source_region_ids:
                pending.source_region_ids.append(value)
        pending.region_qualities.extend(
            value for value in item.region_qualities
            if value not in pending.region_qualities)
        pending.region_issues.extend(
            value for value in item.region_issues
            if value not in pending.region_issues)
        pending.region_processing_config_ids.extend(
            value for value in item.region_processing_config_ids
            if value not in pending.region_processing_config_ids)
    seal()
    return out


def _is_ocr_paragraph_start(text: str) -> bool:
    return bool(
        re.match(r"^第[一二三四五六七八九十百千零〇两\d]+[章节]", text)
        or _OCR_DOTTED_START_RE.match(text)
        or _OCR_LIST_START_RE.match(text)
    )


def _ocr_lines_share_flow(first: _PageItem, second: _PageItem) -> bool:
    if first.page != second.page:
        return False
    if first.source_region_ids and second.source_region_ids:
        if not set(first.source_region_ids).intersection(second.source_region_ids):
            return False
    line_boxes = first.extra.get("source_line_bboxes") or [first.bbox]
    last = line_boxes[-1]
    height = max(1.0, last[3] - last[1], second.bbox[3] - second.bbox[1])
    gap = second.bbox[1] - last[3]
    return -0.25 * height <= gap <= 2.5 * height


def _match_text_regions(
    elem_bbox: list[float],
    region_bboxes: list[tuple[list[float], dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return EVERY upstream text region that overlaps an element's bbox.

    An element may legitimately fall inside several layout text blocks (nested
    or overlapping regions). All participating regions are returned so quality
    can be taken as the strictest over all of them (order-independent) and every
    region id / issue / config is preserved losslessly (R6 one-to-many). A
    region counts as a match when its bbox overlaps the element's bbox by any
    non-zero area. Returns ``[]`` when no region overlaps -- the caller then
    downgrades to ``needs_review`` rather than guessing ``passed`` (T02 P1a:
    cannot trace -> cannot assert passed).
    """
    ex0, ey0, ex1, ey1 = elem_bbox
    matched: list[dict[str, Any]] = []
    for rb, region in region_bboxes:
        rx0, ry0, rx1, ry1 = rb
        ox0, oy0 = max(ex0, rx0), max(ey0, ry0)
        ox1, oy1 = min(ex1, rx1), min(ey1, ry1)
        if ox0 < ox1 and oy0 < oy1:
            matched.append(region)
    return matched


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
    line_bboxes = slot.extra.get("source_line_bboxes") or [slot.bbox]
    source_spans = [
        _make_span(
            span_counter, "primary" if index == 0 else "continuation",
            slot.page, bbox,
        )
        for index, bbox in enumerate(line_bboxes)
    ]
    method, engine_id, confidence = _route_provenance(slot)
    provenance = [_provenance("extract", method, engine_id, confidence)]
    if slot.extra.get("paragraph_reconstruction"):
        provenance.append(_provenance(
            "merge", "ocr", _OCR_PARAGRAPH_RULE_ENGINE_ID, None))
    quality_status, policy_reasons = _derive_text_quality(slot, text)
    if slot.extra.get("v3_role") == "toc":
        quality_status = "needs_review"
        if "toc_navigation_only" not in policy_reasons:
            policy_reasons.append("toc_navigation_only")
    furniture_rule = slot.extra.get("v3_furniture_rule")
    if furniture_rule and f"page_furniture:{furniture_rule}" not in policy_reasons:
        policy_reasons.append(f"page_furniture:{furniture_rule}")
    fusions = list(slot.extra.get("ocr_fusions") or [])
    fusion = slot.extra.get("ocr_fusion")
    if fusion and fusion not in fusions:
        fusions.append(fusion)
    if fusions:
        provenance.append(_provenance(
            "merge", "ocr", "ocr-fusion-1", fusions[0].get("independent_score")))
        quality_status = "needs_review"
        if "independent_ocr_fusion_unreviewed" not in policy_reasons:
            policy_reasons.append("independent_ocr_fusion_unreviewed")
    ocr_text_sources = list(slot.extra.get("ocr_text_sources") or [])
    if ocr_text_sources:
        confidence_values = [value.get("score") for value in ocr_text_sources
                             if isinstance(value.get("score"), (int, float))]
        provenance.append(_provenance(
            "recognize", "ocr", "ocr-region-1",
            min(confidence_values) if confidence_values else None))
        quality_status = "needs_review"
        if "preprocessed_independent_ocr_unreviewed" not in policy_reasons:
            policy_reasons.append("preprocessed_independent_ocr_unreviewed")
    return {
        "element_id": elem_counter.next(),
        "type": "text",
        "role": role,
        "label": label,
        "section_path": section_path,
        "content": {"text": text, "list": _list_info(slot.text)},
        "source_spans": source_spans,
        "provenance": provenance,
        "links": [],
        "quality_status": quality_status,
        "policy_reasons": policy_reasons,
        "source_region_ids": list(slot.source_region_ids),
        "processing_config_ids": list(slot.region_processing_config_ids),
        "quality_issues": list(slot.region_issues),
        **({"ocr_fusion": [dict(value) for value in fusions]} if fusions else {}),
        **({"ocr_text_sources": [dict(value) for value in ocr_text_sources]}
           if ocr_text_sources else {}),
    }


def _mark_recurring_header_footer(
    slots: list[_PageItem],
    page_by_number: dict[int, dict[str, Any]],
) -> None:
    """Mark running header/footer text via cross-page recurrence.

    For each text slot sitting in the top or bottom band of its page, group by
    normalized text + band. If the same short text recurs in the same band on
    >= ``_RECURRING_MIN_PAGES`` pages, every matching slot is tagged with
    ``extra["recurring_role"]`` = "header" (top band) / "footer" (bottom band);
    ``_infer_text_role`` then returns that role so the chunker drops it.
    """
    # band -> normalized_text -> set of pages
    top_pages: dict[str, set[int]] = {}
    bottom_pages: dict[str, set[int]] = {}
    # track which slots belong to each (band, normalized_text)
    tagged_slots: dict[tuple[str, str], list[_PageItem]] = {}

    for slot in slots:
        if slot.kind != "text":
            continue
        raw = (slot.text or "").strip()
        if not raw or len(raw) > _HEADER_FOOTER_MAX_LEN:
            continue
        norm = re.sub(r"\s+", "", raw)
        if not norm:
            continue
        page_row = page_by_number.get(slot.page)
        if not page_row:
            continue
        height = float(page_row.get("height") or 0.0)
        if height <= 0:
            continue
        y0, y1 = slot.bbox[1], slot.bbox[3]
        band = None
        if y0 < height * _HEADER_BAND_RATIO:
            band = "header"
        elif y1 > height * (1 - _FOOTER_BAND_RATIO):
            band = "footer"
        if band is None:
            continue
        key = (band, norm)
        tagged_slots.setdefault(key, []).append(slot)
        pages_map = top_pages if band == "header" else bottom_pages
        pages_map.setdefault(norm, set()).add(slot.page)

    for (band, _norm), group in tagged_slots.items():
        pages_map = top_pages if band == "header" else bottom_pages
        if len(pages_map.get(_norm, set())) >= _RECURRING_MIN_PAGES:
            for slot in group:
                slot.extra["recurring_role"] = band


def _infer_text_role(slot: _PageItem, heading: HeadingNode | None) -> str:
    v3_role = slot.extra.get("v3_role")
    if v3_role:
        if v3_role == "toc":
            return "paragraph"
        if v3_role in {
            "document_title", "heading", "clause", "paragraph", "list_item",
            "header", "footer", "page_number", "watermark", "unknown",
        }:
            return v3_role
    raw = (slot.text or "").strip()
    if _PAGE_NUM_RE.match(raw):
        return "page_number"
    recurring = slot.extra.get("recurring_role")
    if recurring in ("header", "footer"):
        return recurring
    if heading is not None:
        # Short title -> heading; long body -> clause (条文 that anchors a path).
        title = heading.title or ""
        if not title and "." in heading.label:
            return "clause"
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
    if slot.extra.get("v3_block"):
        descriptor = slot.extra.get("v3_heading") or {}
        return descriptor.get("label") or None
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
    parsed_pages: set[int] | None = None,
    page_dims: dict[int, tuple[float, float]] | None = None,
) -> dict[str, Any] | None:
    latex = (slot.extra.get("latex") or "").strip()
    if not latex:
        return None
    formula_number = _extract_formula_number(slot, global_slots)
    context_text, context_spans = _gather_formula_context(slot, global_slots, span_counter)
    primary_span = _make_span(span_counter, "primary", slot.page, slot.bbox)
    section_path = build_section_path(headings, slot.source_index)
    score = slot.extra.get("score")
    region = slot.ref or {}
    detected = region.get("_formula_detected") or {}
    run_meta = region.get("_formula_run") or {}
    # T04-R2: reference the engine that ACTUALLY recognised this region
    # (registered from the cache run provenance) -- never a hardcoded engine.
    engine_id = region.get("_formula_engine_id") or "unknown-1"
    provenance = [
        _provenance(
            "recognize",
            "formula_recognition",
            engine_id,
            round(float(score), 4) if score is not None else None,
        )
    ]
    # T04-R2 lossless source provenance: the verbatim upstream region id (the
    # fx identity is the fallback only when the record carries none), the
    # processing configs on both the detection and the recognition side, the
    # upstream quality issues verbatim, and the real crop asset identity.
    source_id = region.get("_formula_source_id") or detected.get("region_id")
    source_region_ids = [source_id] if source_id else []
    processing_config_ids: list[str] = []
    for cfg in (detected.get("processing_config_id"), run_meta.get("profile")):
        if isinstance(cfg, str) and cfg and cfg not in processing_config_ids:
            processing_config_ids.append(cfg)
    quality_issues = [i for i in detected.get("issues") or [] if isinstance(i, str)]
    for iss in region.get("_formula_outcome", {}).get("issues") or []:
        # assembly-derived issues (gate / missing asset / detection status /
        # relation markers) are appended without duplicating upstream ones
        if iss not in quality_issues:
            quality_issues.append(iss)
    asset = region.get("_formula_asset") or {}
    crop_assets = (
        [{"ref": asset["ref"], "sha256": asset["sha256"]}]
        if isinstance(asset, dict) and asset.get("sha256") else [])
    relations, relation_spans = _formula_relation_records(
        detected, span_counter, parsed_pages or set(), page_dims or {})
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
        "source_spans": [primary_span] + context_spans + relation_spans,
        "provenance": provenance,
        "links": [],
        "quality_status": _STRUCTURED_QUALITY,
        "policy_reasons": list(_STRUCTURED_REASONS),
        "source_region_ids": source_region_ids,
        "processing_config_ids": processing_config_ids,
        "quality_issues": quality_issues,
        "execution_status": "completed",
        "crop_assets": crop_assets,
        "cross_page_relations": relations,
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


def _walk_formula_context(
    slot: _PageItem,
    global_slots: list[_PageItem],
) -> tuple[list[_PageItem], str | None]:
    """Collect the contiguous text slots below a formula (T09 walk).

    Walks forward in reading order over same-page text elements whose bbox sits
    below the formula (within ``_CONTEXT_MAX_GAP``), stopping at the next
    clause / heading / list / caption or another formula / table. This is the
    POSITION + STOP-CONDITION half of the T09 four-requirement binding; the
    context TEXT itself is still built by :func:`_gather_formula_context` on
    top of this walk so the stored ``context_text`` stays byte-identical.

    Returns ``(slots, stop_reason)``. ``stop_reason`` is ``None`` for a clean
    structural end (furniture / next clause / non-text kind) and
    ``"mixed_stop_content"`` when the walk had to stop at a text element that
    MIXES explanation lines with a stop line (e.g. the next clause number
    "5.8.3 …" OCR-merged into the same element) -- such an element can never
    be faithfully attributed to the formula, so it and everything after it are
    conservatively left as body text.
    """
    slots: list[_PageItem] = []
    stop_reason: str | None = None
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
        # T09 remediation: page furniture (page number / running header or
        # footer) is a STRUCTURAL BOUNDARY, never explanation text. It ends
        # the context block just like a clause or heading would.
        if _is_furniture_slot(t):
            break
        # Stop-condition scan PER LINE: a clean stop on the first line ends
        # the block normally; a stop line INSIDE the element (after
        # explanation content) means the whole element mixes attribution --
        # T09 closure: conservative truncation, the element stays body text.
        lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
        if _is_context_stop(s):
            break
        if any(_is_context_stop(ln) for ln in lines[1:]):
            stop_reason = "mixed_stop_content"
            break
        slots.append(t)
    return slots, stop_reason


def _is_furniture_slot(slot: _PageItem) -> bool:
    """True for page furniture slots: page numbers and recurring headers/
    footers. Such elements can never be explanation text and structurally
    END the context walk (T09 remediation)."""
    if _PAGE_NUM_RE.match((slot.text or "").strip()):
        return True
    return (slot.extra or {}).get("recurring_role") in ("header", "footer")


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
    for t in _walk_formula_context(slot, global_slots)[0]:
        pieces.append(_normalise_text((t.text or "").strip()))
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


# T09: the explicit START-SIGNAL of a formula explanation block. Only a block
# that literally opens with "式中" (optionally followed by a colon/space) can
# become a primary explanation -- anything else is never a binding, and the
# situation is recorded as an explicit ambiguity instead of being guessed.
_EXPLANATION_START_RE = re.compile(r"^式\s*中\s*[:：]?")


def _resolve_explanation_bindings(
    slot: _PageItem,
    global_slots: list[_PageItem],
    slot_element_ids: dict[int, str],
    headings: list[HeadingNode],
) -> tuple[list[dict[str, Any]], list[str]]:
    """T09 four-requirement primary binding between a formula and its "式中"
    explanation text elements.

    A PRIMARY binding requires ALL of:

    1. explicit start signal -- the first contiguous text slot below the
       formula literally opens with ``式中`` (``_EXPLANATION_START_RE``);
    2. same section path -- every explanation slot's section path (built from
       the heading hierarchy) equals the formula's (a heading between formula
       and explanation means a different section -> ambiguous);
    3. position -- the contiguous block sits directly below the formula within
       ``_CONTEXT_MAX_GAP`` (enforced by :func:`_walk_formula_context`);
    4. stop conditions -- clause / heading / list / caption / next formula /
       table end the block (``_is_context_stop`` + the kind walk).

    Any unmet requirement (with candidate text present) produces NO binding
    and an explicit ``formula_explanation_binding_ambiguous:<reason>`` issue:
    the explanation stays an independent text element. A formula with no text
    below it at all is simply unbound -- that is not an ambiguity.
    """
    candidates, stop_reason = _walk_formula_context(slot, global_slots)
    issues: list[str] = []
    if stop_reason:
        # the walk was truncated at a text element mixing explanation with a
        # stop line (next clause / next formula) -- recorded whether or not a
        # partial binding survived, so the truncation is always explicit.
        issues.append(f"formula_explanation_binding_ambiguous:{stop_reason}")
    if not candidates:
        return [], issues
    own_path = [p["label"] for p in build_section_path(headings, slot.source_index)]
    first = (candidates[0].text or "").strip()
    if not _EXPLANATION_START_RE.match(first):
        issues.append("formula_explanation_binding_ambiguous:no_start_signal")
        return [], issues
    for t in candidates:
        other_path = [p["label"] for p in
                      build_section_path(headings, t.source_index)]
        if other_path != own_path:
            issues.append(
                "formula_explanation_binding_ambiguous:section_path_mismatch")
            return [], issues
    bindings: list[dict[str, Any]] = []
    for order, t in enumerate(candidates):
        element_id = slot_element_ids.get(t.source_index)
        if not element_id:
            # the explanation slot was consumed by another structured element
            # (dedup) or produced no element -- never a partial binding.
            issues.append(
                "formula_explanation_binding_ambiguous:explanation_element_missing")
            return [], issues
        bindings.append({
            "element_id": element_id,
            "region_id": (t.ref or {}).get("region_id"),
            "basis": "below_geometry",
            "role": "primary",
            "order": order,
        })
    return bindings, issues


# T09 remediation (review 2026-09-28): the geometry walk is a GUESS. An
# unbound formula must not RETAIN the guessed association: the stored
# ``context_text`` is cleared, the walk-derived context spans are removed,
# and the unbound candidate text elements are marked so the chunker keeps
# them out of the surrounding prose. The explanation TEXT itself is never
# deleted -- it lives on in its own independent text elements.
UNBOUND_CONTEXT_DROPPED = "formula_context_text_dropped:unbound"
_UNBOUND_CANDIDATE_PREFIX = "formula_explanation_unbound:"


def _drop_unbound_formula_context(
    el: dict[str, Any],
    candidate_ids: list[str],
    elements_by_id: dict[str, dict[str, Any]],
) -> str | None:
    """Strip the geometry-guessed context from a formula with NO primary
    binding; mark its unbound explanation candidates for chunk isolation.

    Returns the audit issue code when something was actually dropped (the
    stored ``context_text`` or walk spans existed), else ``None`` -- a
    formula that never had context is not "stripped", just silent.
    """
    had_text = bool(el.get("content", {}).get("context_text"))
    had_spans = any(s.get("role") == "context" for s in el.get("source_spans") or [])
    if had_text or had_spans:
        el["content"]["context_text"] = None
        el["source_spans"] = [s for s in el.get("source_spans") or []
                              if s.get("role") != "context"]
        if UNBOUND_CONTEXT_DROPPED not in el["quality_issues"]:
            el["quality_issues"].append(UNBOUND_CONTEXT_DROPPED)
        issue: str | None = UNBOUND_CONTEXT_DROPPED
    else:
        issue = None
    fx_id = el["element_id"]
    for cid in candidate_ids:
        target = elements_by_id.get(cid)
        if target is None:
            continue
        marker = _UNBOUND_CANDIDATE_PREFIX + fx_id
        if marker not in (target.get("quality_issues") or []):
            target.setdefault("quality_issues", []).append(marker)
    return issue


def _resolve_cross_page_explanation_bindings(
    element: dict[str, Any],
    elements_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """T09 cross-page explanation bindings from EXPLICIT relations only.

    Only ``explicit_numbered_formula_parameter_reference`` relations may bind
    (the relation's SOURCE block is the explanation text that names the
    formula by number). The binding target is the canonical text element whose
    primary span sits on the relation source's page with the same bbox; zero
    or multiple matches produce an explicit unresolved issue and NO binding.
    ``ambiguous_*`` / ``uncertain_*`` relation kinds NEVER bind.
    """
    existing = {(b["element_id"], b["role"], b["basis"])
                for b in element.get("explanation_bindings") or []}
    # The merged binding list (existing primary/geometry bindings + any new
    # cross-page bindings) so callers can assign it back wholesale.
    bindings: list[dict[str, Any]] = [
        dict(b) for b in element.get("explanation_bindings") or []
    ]
    issues: list[str] = []
    order = max((b["order"] for b in element.get("explanation_bindings") or []),
                default=-1)
    for rel in element.get("cross_page_relations") or []:
        if rel.get("kind") != "explicit_numbered_formula_parameter_reference":
            continue  # ambiguous/uncertain relations never bind
        source = rel.get("source") or {}
        page = source.get("physical_page")
        bbox = source.get("bbox")
        if not isinstance(page, int) or not isinstance(bbox, (list, tuple)):
            issues.append("cross_page_explanation_binding_unresolved:no_source_position")
            continue
        matches = [
            e for e in elements_by_id.values()
            if e["type"] == "text"
            and any(s["physical_page"] == page
                    and all(abs(a - c) <= 0.5 for a, c in zip(s["bbox"], bbox))
                    for s in e.get("source_spans") or [])
        ]
        if len(matches) != 1:
            issues.append(
                "cross_page_explanation_binding_unresolved:"
                f"page={page} matches={len(matches)}")
            continue
        target_id = matches[0]["element_id"]
        if any(eid == target_id for eid, _r, _b in existing):
            continue  # already bound (e.g. primary from geometry) -- never dup
        order += 1
        bindings.append({
            "element_id": target_id,
            "region_id": source.get("region_id"),
            "basis": "explicit_number_reference",
            "role": "secondary",
            "order": order,
        })
        existing.add((target_id, "secondary", "explicit_number_reference"))
    return bindings, issues


# --------------------------------------------------------------------------- #
# Table element -- faithful structured consumption (T03)
# --------------------------------------------------------------------------- #


# Upstream region/table execution-status vocabulary -> element-level status.
# Anything not in this closed set is treated as unknown (conservative).
# Repo-defined table recognition routes (unified_pdf semantics): the cropped
# local-table model is the primary route; the others are fallbacks attempted
# only after the primary failed. Lower rank = higher priority.
_TABLE_ROUTE_PRIORITY = {
    "cropped_table": 0,
    "full_page_fallback": 1,
    "ocr_geometry_fallback": 2,
}
# Outcome execution vocabulary (D1 separation). ``not_detected`` is
# deliberately ABSENT: an outcome exists only for a DETECTED table region, so
# "not detected" can never be a legal outcome status. ``needs_review`` is a
# QUALITY verdict (it lives in ``quality_status``), never an execution status:
# a detected region without a legal structured result -- or with an ambiguous
# selection -- closes as failed / unsupported with quality_status="needs_review".
_OUTCOME_EXEC_STATUSES = {"completed", "failed", "unsupported"}


def _candidate_route(tbl: dict[str, Any]) -> str:
    """Repo-defined route of a structured result: the route of the attempt
    that actually COMPLETED and produced the cells.

    A FAILED ``cropped_table`` attempt must never promote a result to
    cropped_table (REJECT issue 3): when attempts are recorded, only
    COMPLETED attempts define the route. Results WITHOUT any recorded
    attempts are successful primary cropped recognitions (unified_pdf
    records attempts only on fallback paths). Results with recorded
    attempts but none completed have NO trusted route ("" -- they can never
    back a completed element; see ``_has_completed_attempt``).
    """
    attempts = tbl.get("processing_attempts") or []
    completed = [a for a in attempts if a.get("execution_status") == "completed"]
    routes = [a.get("route") for a in completed if a.get("route")]
    if routes:
        return sorted(routes, key=lambda r: _TABLE_ROUTE_PRIORITY.get(r, 99))[0]
    if attempts:
        return ""  # recorded attempts, none completed: untrusted
    return "cropped_table"


def _has_completed_attempt(tbl: dict[str, Any]) -> bool:
    """True when the result can legally back a COMPLETED element.

    Results without recorded attempts are repo-defined successful primary
    recognitions; results WITH recorded attempts need at least one completed
    attempt -- cells without a completed attempt are contradictory evidence,
    never a silent success (REJECT issue 2).
    """
    attempts = tbl.get("processing_attempts") or []
    if not attempts:
        return True
    return any(a.get("execution_status") == "completed" for a in attempts)


def _intersection_area(a: Any, b: Any) -> float:
    """Intersection area of two [x0, y0, x1, y1] bboxes (0.0 when invalid)."""
    if not (isinstance(a, list) and isinstance(b, list)
            and len(a) == 4 and len(b) == 4):
        return 0.0
    ox0, oy0 = max(a[0], b[0]), max(a[1], b[1])
    ox1, oy1 = min(a[2], b[2]), min(a[3], b[3])
    if ox0 >= ox1 or oy0 >= oy1:
        return 0.0
    return (ox1 - ox0) * (oy1 - oy0)


def _bbox_area(box: Any) -> float:
    """Area of one [x0, y0, x1, y1] bbox (0.0 when invalid)."""
    if not isinstance(box, list) or len(box) != 4:
        return 0.0
    x0, y0, x1, y1 = box
    if x0 >= x1 or y0 >= y1:
        return 0.0
    return float((x1 - x0) * (y1 - y0))


def _admissible_tables(region: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured results of a region that carry a non-empty ``cells`` list."""
    out = []
    for tbl in region.get("tables") or []:
        cells = tbl.get("cells")
        if isinstance(cells, list) and cells:
            out.append(tbl)
    return out


def _select_structured_table(
    region: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    """Deterministically select the authoritative structured table result.

    Returns ``(selected, basis_or_failure, admissible)``. The selection is
    array-order-INDEPENDENT: swapping ``region["tables"]`` cannot change the
    outcome, because every tie-break is based on recorded evidence, never on
    list position:

    1. ``single_admissible`` -- exactly one result with legal cells;
    2. ``explicit_selection`` -- an explicit upstream selection marker
       (``region["selected_table_element_id"]`` or a ``selected`` flag on the
       result); a marker naming a missing/ambiguous id fails loudly;
    3. ``route_priority`` -- the repo-defined route order
       (cropped_table > full_page_fallback > ocr_geometry_fallback) when the
       candidates' routes differ;
    4. ``max_region_overlap`` -- the unique maximal bbox intersection with the
       region (geometry evidence);
    5. ``min_enclosing_excess`` -- when tied candidates all fully enclose the
       region, the unique tightest crop wins; equal-size ties remain ambiguous;
    6. ``ambiguous_selection`` / ``selected_table_not_found`` -- no sufficient
       basis: the caller records an explicit failure outcome (execution
       status "failed", quality verdict "needs_review") and does NOT emit an
       arbitrary TableElement.
    """
    tables = _admissible_tables(region)
    if not tables:
        return None, None, tables
    if len(tables) == 1:
        return tables[0], "single_admissible", tables

    # 1. Explicit upstream selection evidence.
    sel_id = region.get("selected_table_element_id")
    marked = [t for t in tables if t.get("selected")]
    if sel_id:
        matches = [t for t in tables if t.get("element_id") == sel_id]
        if len(matches) == 1:
            return matches[0], "explicit_selection", tables
        return None, "selected_table_not_found", tables
    if marked:
        if len(marked) == 1:
            return marked[0], "explicit_selection", tables
        return None, "ambiguous_selection", tables

    # 2. Repo-defined route priority narrows the candidate set to the best
    #    route present; a fallback result must never beat a primary one.
    routes = {_candidate_route(t) for t in tables}
    if len(routes) > 1:
        best = min(routes, key=lambda r: _TABLE_ROUTE_PRIORITY.get(r, 99))
        candidates = [t for t in tables if _candidate_route(t) == best]
        if len(candidates) == 1:
            return candidates[0], "route_priority", tables
    else:
        candidates = tables

    # 3. Unique maximal bbox overlap with the region (within the candidate set).
    rb = region.get("bbox")
    if isinstance(rb, list) and len(rb) == 4:
        areas = [_intersection_area(rb, t.get("bbox")) for t in candidates]
        mx = max(areas)
        if mx > 0.0:
            winners = [t for t, a in zip(candidates, areas) if a == mx]
            if len(winners) == 1:
                return winners[0], "max_region_overlap", tables
            region_area = _bbox_area(rb)
            if (region_area > 0.0
                    and all(abs(a - region_area) <= 1e-6 for a in areas)):
                candidate_areas = [_bbox_area(t.get("bbox")) for t in candidates]
                positive = [a for a in candidate_areas if a > 0.0]
                if positive:
                    smallest = min(positive)
                    tightest = [
                        t for t, area in zip(candidates, candidate_areas)
                        if abs(area - smallest) <= 1e-6
                    ]
                    if len(tightest) == 1:
                        return tightest[0], "min_enclosing_excess", tables

    return None, "ambiguous_selection", tables


def _build_table_cells_faithful(
    struct_table: dict[str, Any], span_counter: _Counter, pno: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Convert the upstream structured cells into Canonical TableCell dicts.

    Consumes the model's real ``row``/``col``/``row_span``/``col_span``/
    ``cell_role``/``text``/``bbox`` verbatim -- never heuristically rebuilt from
    text positions. ``col`` -> ``column`` / ``col_span`` -> ``column_span`` /
    ``cell_role`` -> ``role`` (schema field names). Cell bboxes are carried as
    ``source_bbox`` (content) so a geometry change is a content-identity change.
    """
    raw_cells = struct_table.get("cells") or []
    # Canonical cell order: sort by grid coordinates so the content identity is
    # independent of the (arbitrary) upstream emission order.
    raw_cells = sorted(
        raw_cells,
        key=lambda rc: (int(rc.get("row", 0)), int(rc.get("col", rc.get("column", 0)))),
    )
    cells: list[dict[str, Any]] = []
    max_row = 0
    max_col = 0
    max_row_cover = 0   # max(row + row_span): minimal grid covering all spans
    max_col_cover = 0
    for rc in raw_cells:
        row = int(rc.get("row", 0))
        col = int(rc.get("col", rc.get("column", 0)))
        row_span = int(rc.get("row_span", 1))
        col_span = int(rc.get("col_span", rc.get("column_span", 1)))
        role = rc.get("cell_role") or rc.get("role") or "unknown"
        if role not in ("header", "stub", "data", "unknown"):
            role = "unknown"
        text = rc.get("text") or ""
        bbox = rc.get("bbox")
        cell: dict[str, Any] = {
            "row": row, "column": col,
            "row_span": row_span, "column_span": col_span,
            "role": role, "text": text,
            "source_span_ids": [],
        }
        if isinstance(bbox, list) and len(bbox) == 4:
            cell["source_bbox"] = [float(v) for v in bbox]
        conf = rc.get("confidence")
        if conf is not None:
            cell["confidence"] = float(conf)
        cells.append(cell)
        max_row = max(max_row, row)
        max_col = max(max_col, col)
        max_row_cover = max(max_row_cover, row + max(row_span, 1))
        max_col_cover = max(max_col_cover, col + max(col_span, 1))
    # row_count / column_count: prefer the model's declared dimensions; fall
    # back to the minimal grid that covers every declared cell AND its spans
    # (upstream results carry no declared dimensions; max(row)+1 alone would
    # clip merged cells out of the grid). Never fabricated beyond coverage.
    row_count = int(struct_table.get("row_count") or max_row_cover)
    column_count = int(struct_table.get("column_count") or max_col_cover)
    return cells, row_count, column_count


def _derive_attempt_id(raw: dict[str, Any]) -> str:
    """Stable attempt id derived from the attempt's recorded evidence.

    Content-derived (NOT position-derived) so the id survives any reordering
    of the attempts list. ``attempt_id`` is "att-" + 12 hex of sha256 over the
    full evidence tuple.
    """
    stable = repr((
        raw.get("route"), raw.get("execution_status"),
        raw.get("engine_name") if raw.get("engine_name") is not None else raw.get("engine"),
        raw.get("engine_version"),
        raw.get("processing_config_id") if raw.get("processing_config_id") is not None else raw.get("config_id"),
        raw.get("config_hash"),
        raw.get("input_sha256") if raw.get("input_sha256") is not None else raw.get("input_id"),
        raw.get("output_sha256") if raw.get("output_sha256") is not None else raw.get("output_id"),
        raw.get("confidence"),
        raw.get("reason") if raw.get("reason") is not None else raw.get("failure_reason"),
    ))
    return "att-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]


def _normalize_attempt(
    raw: dict[str, Any], *, result_id: str | None, input_identity: str | None,
) -> dict[str, Any]:
    """Losslessly normalise one upstream attempt record, bound to its result.

    Every audit-relevant field is preserved (REJECT fix #1): attempt id, route,
    execution status, engine name/version, config id/hash, input/output
    identity, confidence and failure reason. ``result_id`` binds the attempt
    to its candidate result identity (REJECT fix #2: the ledger must show
    WHICH result every attempt belongs to). Absent upstream fields stay
    ``None`` -- never fabricated. ``selected`` starts False; the caller marks
    exactly one attempt when an element is emitted.
    """
    return {
        "attempt_id": raw.get("attempt_id") or _derive_attempt_id(raw),
        "route": raw.get("route"),
        "execution_status": raw.get("execution_status"),
        "engine_name": raw.get("engine_name") if raw.get("engine_name") is not None else raw.get("engine"),
        "engine_version": raw.get("engine_version"),
        "config_id": raw.get("processing_config_id") if raw.get("processing_config_id") is not None else raw.get("config_id"),
        "config_hash": raw.get("config_hash"),
        "input_identity": (
            raw.get("input_sha256") if raw.get("input_sha256") is not None
            else raw.get("input_id") if raw.get("input_id") is not None
            else raw.get("input_identity")
        ) or input_identity,
        "output_identity": (
            raw.get("output_sha256") if raw.get("output_sha256") is not None
            else raw.get("output_id") if raw.get("output_id") is not None
            else raw.get("output_identity")
        ) or result_id,
        "confidence": raw.get("confidence"),
        "failure_reason": raw.get("reason") if raw.get("reason") is not None else raw.get("failure_reason"),
        "result_id": result_id,
        "selected": False,
    }


def _synthesize_attempt(
    region_id: str | None, result_id: str | None, *, completed: bool,
    route: str = "cropped_table", failure_reason: str | None = None,
    config_id: str | None = None,
) -> dict[str, Any]:
    """One synthesised attempt from repo-defined pipeline semantics.

    Used ONLY when upstream recorded no attempts for a result (unified_pdf
    writes attempt records on fallback paths, so a result with legal cells
    and no records is a successful primary cropped recognition; a result with
    no recovered cells failed its primary recognition). All identity fields
    that upstream does not record stay ``None`` -- never fabricated.
    """
    seed = (region_id, result_id, route, "completed" if completed else "failed",
            failure_reason)
    return {
        "attempt_id": "att-" + hashlib.sha256(
            repr(seed).encode("utf-8")).hexdigest()[:12],
        "route": route,
        "execution_status": "completed" if completed else "failed",
        "engine_name": None, "engine_version": None,
        "config_id": config_id, "config_hash": None,
        "input_identity": region_id, "output_identity": result_id,
        "confidence": None, "failure_reason": failure_reason,
        "result_id": result_id,
        "selected": False,
    }


def _sorted_region_results(region: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidate results in canonical (element_id) order.

    The iteration order of ``region["tables"]`` must never influence the
    ledger; sorting by stable result identity makes it array-order-irrelevant.
    """
    return sorted(
        (region.get("tables") or []),
        key=lambda t: (str(t.get("element_id") or ""),
                       repr(t.get("bbox"))),
    )


def _build_attempt_ledger(
    region: dict[str, Any], *, failure_reason: str | None = None,
) -> list[dict[str, Any]]:
    """FULL attempt ledger for a region: every attempt of EVERY candidate.

    (REJECT issue 2: the outcome must save all candidates' attempts, not only
    the selected result's or the first failure's.) Per candidate result:

    * recorded attempts -> all preserved verbatim (normalised, result-bound);
    * no records + non-empty cells -> one synthesised completed primary
      attempt (repo semantics);
    * no records + empty cells -> one synthesised failed attempt.

    A region with NO results synthesises a single region-level failed attempt
    (bound to no result). The returned list is canonically ordered and
    nothing is marked selected -- the caller marks exactly one attempt only
    when a completed element is emitted.
    """
    region_id = region.get("region_id")
    config_id = region.get("processing_config_id")
    entries: list[dict[str, Any]] = []
    for tbl in _sorted_region_results(region):
        result_id = tbl.get("element_id") or None
        raw = tbl.get("processing_attempts") or []
        if raw:
            entries.extend(
                _normalize_attempt(a, result_id=result_id, input_identity=region_id)
                for a in raw
            )
        elif tbl.get("cells"):
            entries.append(_synthesize_attempt(
                region_id, result_id, completed=True, config_id=config_id))
        else:
            entries.append(_synthesize_attempt(
                region_id, result_id, completed=False,
                failure_reason=failure_reason or "table_cells_not_recovered",
                config_id=config_id))
    if not entries:
        entries.append(_synthesize_attempt(
            region_id, None, completed=False,
            failure_reason=failure_reason or "no_structured_table_result",
            config_id=config_id))
    return _stabilise_attempt_ids(entries)


def _stabilise_attempt_ids(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort attempts canonically and make ids unique under any input order.

    Sorted by (attempt_id, result_id, route, execution_status); derived ids
    that collide (identical evidence tuples) get a deterministic occurrence
    suffix so the stored attempt list -- and therefore the digest -- is
    order-invariant.
    """
    ordered = sorted(
        attempts,
        key=lambda a: (str(a.get("attempt_id")), str(a.get("result_id")),
                       str(a.get("route")), str(a.get("execution_status"))),
    )
    seen: dict[str, int] = {}
    for a in ordered:
        aid = str(a.get("attempt_id"))
        n = seen.get(aid, 0)
        seen[aid] = n + 1
        if n:
            a["attempt_id"] = f"{aid}#{n + 1}"
    return ordered


def _mark_selected_attempt(
    attempts: list[dict[str, Any]], selected_result_id: str | None,
) -> str | None:
    """Mark the ONE attempt that produced the emitted element (or None).

    Only a COMPLETED attempt OF THE SELECTED RESULT is eligible (REJECT
    issues 2/3: a selected-but-failed attempt, or one belonging to another
    candidate, must never back a completed outcome). Among the eligible
    attempts the best repo-defined route wins, then the lowest stable
    attempt id -- deterministic and unique by construction. ``None`` means
    no eligible attempt exists: the caller must NOT emit a completed element.
    """
    if not selected_result_id:
        return None
    pool = [
        a for a in attempts
        if a.get("result_id") == selected_result_id
        and a.get("execution_status") == "completed"
    ]
    if not pool:
        return None

    def _rank(a: dict[str, Any]) -> tuple[int, str]:
        return (_TABLE_ROUTE_PRIORITY.get(a.get("route"), 99), str(a.get("attempt_id")))

    chosen = min(pool, key=_rank)
    for a in attempts:
        a["selected"] = a is chosen
    return chosen["attempt_id"]


def _table_outcome(
    region: dict[str, Any], pno: int, *, element_id: str | None,
    execution_status: str, quality_status: str, issues: list[str],
    attempts: list[dict[str, Any]], selection_basis: str | None,
    admissible_ids: list[str | None], selected_result_id: str | None,
    selected_attempt_id: str | None,
) -> dict[str, Any]:
    """Build the closed outcome-ledger entry for one detected table region.

    Exactly ONE outcome per region: completed + the element id + the full
    attempt ledger + the selection basis, or an explicit failure with no
    element and no selected attempt.
    """
    return {
        "region_id": region.get("region_id"),
        "kind": "table",
        "physical_page": pno,
        "bbox": list(region["bbox"]) if region.get("bbox") else None,
        "execution_status": execution_status,
        "quality_status": quality_status,
        "issues": list(issues),
        "processing_config_id": region.get("processing_config_id"),
        "element_id": element_id,
        "selection_basis": selection_basis,
        "admissible_result_ids": sorted(
            str(t.get("element_id") or "") for t in (admissible_ids or [])
        ),
        "selected_result_id": selected_result_id,
        "selected_attempt_id": selected_attempt_id,
        "attempts": attempts,
    }


def _build_table_element(
    slot: _PageItem,
    global_slots: list[_PageItem],
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Faithfully assemble a TableElement plus its closed outcome ledger entry.

    Consumes ``region["tables"][i]`` cells / spans / header roles / caption /
    table number / unit / footnotes verbatim. Never rebuilds cells from text
    positions. Returns ``(element, outcome)``:

    * success -- a structurally legal TableElement and a ``completed`` outcome
      carrying the FULL attempt ledger (REJECT fix #1), the deterministic
      selection basis (REJECT fix #3) and exactly one selected attempt;
    * failure -- ``element is None`` and an explicit failed / unsupported
      outcome whose ``quality_status`` is ``needs_review`` (REJECT fix #2: a
      detected region without a legal structured result is NEVER completed
      and NEVER not_detected; D1: execution and quality vocabularies are
      disjoint).

    Cross-page ``continues`` links are NOT resolved here: they are resolved in
    a second pass once the region->element registry is complete, so forward
    references resolve and missing/conflicting targets become explicit issues
    (REJECT fix #4).
    """
    pno = slot.page
    region = slot.ref or {}
    region_bbox = slot.bbox
    region_id = region.get("region_id")

    struct_table, basis, admissible = _select_structured_table(region)
    admissible_ids = [t for t in admissible]
    selected_result_id = (struct_table or {}).get("element_id")

    if struct_table is None:
        # No deterministic selection -> explicit failure outcome, no element.
        # A detected region is NEVER completed here and NEVER not_detected.
        if basis == "selected_table_not_found":
            exec_status = "failed"
            reason_code = "selected_table_not_found"
        elif basis == "ambiguous_selection":
            # No sufficient selection basis -> explicit FAILURE with a
            # needs_review QUALITY verdict (D1 separation: execution_status
            # and quality_status are disjoint vocabularies). No arbitrary
            # TableElement is emitted (REJECT fix #3).
            exec_status = "failed"
            reason_code = "ambiguous_structured_table_selection"
        elif region.get("execution_status") == "unsupported":
            exec_status = "unsupported"
            reason_code = "table_region_unsupported"
        else:
            exec_status = "failed"
            reason_code = ("no_structured_table_result"
                           if not (region.get("tables"))
                           else "table_cells_not_recovered")
        issues = list(region.get("issues") or [])
        if reason_code not in issues:
            issues.append(reason_code)
        # FULL attempt ledger: every recorded attempt of EVERY candidate
        # result (plus the synthesised per-result / region-level fallback
        # attempts). Nothing is marked selected -- nothing was emitted
        # (REJECT fix #1/#2).
        attempts = _build_attempt_ledger(region, failure_reason=reason_code)
        outcome = _table_outcome(
            region, pno, element_id=None, execution_status=exec_status,
            quality_status="needs_review", issues=issues, attempts=attempts,
            selection_basis=None, admissible_ids=admissible_ids,
            selected_result_id=None, selected_attempt_id=None)
        return None, outcome

    # A selected result whose recorded attempts are ALL failed must never
    # yield a completed TableElement -- cells without a completed attempt are
    # contradictory evidence; the outcome closes as an explicit failure with
    # the full ledger unselected (REJECT issue 2).
    if not _has_completed_attempt(struct_table):
        reason_code = "selected_result_has_no_completed_attempt"
        issues = list(region.get("issues") or [])
        if reason_code not in issues:
            issues.append(reason_code)
        attempts = _build_attempt_ledger(region, failure_reason=reason_code)
        outcome = _table_outcome(
            region, pno, element_id=None, execution_status="failed",
            quality_status="needs_review", issues=issues, attempts=attempts,
            selection_basis=None, admissible_ids=admissible_ids,
            selected_result_id=None, selected_attempt_id=None)
        return None, outcome

    cells, row_count, column_count = _build_table_cells_faithful(
        struct_table, span_counter, pno)
    if not cells or row_count <= 0 or column_count <= 0:
        # Defensive (selection only admits non-empty cells): explicit failed
        # outcome, no fake table.
        issues = list(region.get("issues") or []) + ["table_cells_not_recovered"]
        attempts = _build_attempt_ledger(
            region, failure_reason="table_cells_not_recovered")
        outcome = _table_outcome(
            region, pno, element_id=None, execution_status="failed",
            quality_status="needs_review", issues=issues, attempts=attempts,
            selection_basis=None, admissible_ids=admissible_ids,
            selected_result_id=None, selected_attempt_id=None)
        return None, outcome

    caption = struct_table.get("caption") or None
    table_number = struct_table.get("table_number") or None
    unit_context = list(struct_table.get("unit_context") or [])
    # Footnotes -> table notes (bound to this table).
    notes: list[dict[str, Any]] = []
    for fn in (struct_table.get("footnotes") or []):
        text = fn.get("text") or ""
        if text:
            notes.append({"text": text, "source_span_ids": []})

    primary_span = _make_span(span_counter, "primary", pno, region_bbox)
    section_path = build_section_path(headings, slot.source_index)
    element_id = elem_counter.next()

    # Provenance: layout detection + the structured recognition route.
    method = "table_recognition"
    provenance = [
        _provenance("detect", "layout_detection", "layout-1", None),
        _provenance("recognize", method, "layout-1", None),
    ]

    # Quality: structured content defaults to needs_review (D3) regardless of
    # recognition route success; execution_status is carried for audit (R5).
    issues = list(region.get("issues") or [])
    if struct_table.get("review_reasons"):
        for r in struct_table["review_reasons"]:
            if r not in issues:
                issues.append(r)
    if (region.get("execution_status") or "completed") != "completed":
        # A legal structured result exists, so the recognition of THIS result
        # completed; a stale region-level failure status is an inconsistency,
        # recorded -- not silently promoted.
        if "region_status_inconsistent_with_structured_result" not in issues:
            issues.append("region_status_inconsistent_with_structured_result")

    # FULL attempt ledger across ALL candidate results; exactly one selected
    # attempt -- a COMPLETED attempt of the SELECTED result -- referenced
    # from the outcome by its stable id together with the selected result id
    # (REJECT fix #1/#2/#3).
    attempts = _build_attempt_ledger(region)
    selected_attempt_id = _mark_selected_attempt(attempts, selected_result_id)
    if selected_attempt_id is None:
        # Defensive: no completed attempt of the selected result exists in
        # the ledger -> no completed element (mirrors the recorded-attempts
        # gate above for any other ledger shape).
        reason_code = "selected_result_has_no_completed_attempt"
        issues = list(region.get("issues") or [])
        if reason_code not in issues:
            issues.append(reason_code)
        outcome = _table_outcome(
            region, pno, element_id=None, execution_status="failed",
            quality_status="needs_review", issues=issues, attempts=attempts,
            selection_basis=None, admissible_ids=admissible_ids,
            selected_result_id=None, selected_attempt_id=None)
        return None, outcome

    # Consumed plain-text sources (text/table dedup traceability, REJECT
    # requirement 6): upstream element id, page and bbox of every text element
    # consumed by THIS region -- preserved, never silently dropped.
    consumed_sources = list(slot.extra.get("consumed_sources") or [])

    outcome = _table_outcome(
        region, pno, element_id=element_id, execution_status="completed",
        quality_status=_STRUCTURED_QUALITY, issues=issues, attempts=attempts,
        selection_basis=basis, admissible_ids=admissible_ids,
        selected_result_id=selected_result_id,
        selected_attempt_id=selected_attempt_id)

    label = table_number
    element = {
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
            "notes": notes,
            "table_number": table_number,
            "unit_context": unit_context,
        },
        "source_spans": [primary_span],
        "provenance": provenance,
        # Continuation links are resolved in the second pass (phase 2), after
        # the full region->element registry exists.
        "links": [],
        "quality_status": _STRUCTURED_QUALITY,
        "policy_reasons": list(_STRUCTURED_REASONS),
        "source_region_ids": [region_id] if region_id else [],
        "processing_config_ids": [region["processing_config_id"]]
        if region.get("processing_config_id") else [],
        "quality_issues": issues,
        "execution_status": "completed",
        "consumed_sources": consumed_sources,
    }
    return element, outcome



# --------------------------------------------------------------------------- #
# Figure element
# --------------------------------------------------------------------------- #


def _figure_pi_caption_text(region: dict[str, Any] | None) -> str | None:
    """The page-intermediate's structured caption text, whitespace-normalised.

    Returns ``None`` when the upstream recorded no caption (e.g.
    ``caption_not_found``) -- callers must never fall back to nearby-text
    heuristics (T05: the caption binding is the upstream's explicit record,
    not a regex guess).
    """
    if not isinstance(region, dict):
        return None
    cap = region.get("caption")
    text = cap.get("text") if isinstance(cap, dict) else cap
    if isinstance(text, str) and text.strip():
        return " ".join(text.split())
    return None


def _figure_caption_anchor_basis(
    region: dict[str, Any] | None, slot: "_PageItem"
) -> str | None:
    """Explicit identity binding between a PI figure caption and a text slot.

    T05-R2: consumption of a caption text element requires an explicit,
    uniquely checkable identity -- the page-intermediate caption block (or a
    caption-kind relation source) carries an ``element_id`` or a bbox that
    matches the text slot (bbox anchors are page-scoped and compared at 2-dec
    precision, the document's own identity convention). Returns the binding
    basis (``"element_id"`` / ``"bbox"``) or ``None`` when only the caption
    TEXT matched -- text equality alone never consumes.
    """
    if not isinstance(region, dict):
        return None
    blocks: list[Any] = [region.get("caption")]
    for rel in region.get("relations") or []:
        if isinstance(rel, dict) and rel.get("kind") == "caption":
            blocks.append(rel.get("source"))
    slot_eid = (slot.ref or {}).get("element_id")
    slot_bbox = ([round(float(v), 2) for v in slot.bbox]
                 if slot.bbox else None)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        eid = block.get("element_id")
        if (isinstance(eid, str) and eid.strip()
                and isinstance(slot_eid, str) and slot_eid == eid.strip()):
            return "element_id"
        anchor_bbox = _coerce_bbox(block.get("bbox"))
        if (anchor_bbox is not None and slot_bbox is not None
                and block.get("physical_page") == slot.page
                and [round(float(v), 2) for v in anchor_bbox] == slot_bbox):
            return "bbox"
    return None


def _build_figure_element(
    slot: _PageItem,
    global_slots: list[_PageItem],
    heading_by_index: dict[int, HeadingNode],
    headings: list[HeadingNode],
    span_counter: _Counter,
    elem_counter: _Counter,
    parsed_pages: set[int] | None = None,
    page_dims: dict[int, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """Faithful FigureElement assembly from the page-intermediate (T05).

    * caption / figure_number: ONLY the PI structured fields (``caption``,
      ``figure_number``) -- an upstream ``caption_not_found`` stays a missing
      caption and the label stays None; no regex derivation.
    * asset: ONLY the PI's real ``image_ref`` / ``image_sha256``, verified by
      :func:`_figure_verified_asset` (file exists, digest matches, true pixel
      size read from the image header). The old placeholder asset (fabricated
      ref + sha-of-ref + pt->px pixel guess) is gone.
    * references / relations: ONLY the PI's explicit relations (verbatim kind,
      basis, verified, source page/bbox/text) projected into the closed
      relation records (``irel-`` ids) + context source spans on parsed pages;
      body text is never scanned for references.
    * provenance: verbatim source region id, processing config, quality
      issues, execution status and the crop-asset audit trail.
    """
    region = slot.ref or {}
    pno = slot.page
    bbox = slot.bbox
    caption = _figure_pi_caption_text(region)
    figure_number = region.get("figure_number")
    label = (figure_number.strip() if isinstance(figure_number, str)
             and figure_number.strip() else None)
    asset = region.get("_figure_asset")
    crop_assets = region.get("_figure_crop_assets") or []
    outcome = region.get("_figure_outcome") or {}
    issues = _figure_issues(region)
    for iss in outcome.get("issues") or []:
        if iss not in issues:
            issues.append(iss)
    # Structured relation records + context spans (parsed pages only). The
    # element projection is authoritative: the completed outcome is synced to
    # these records afterwards.
    records, rel_spans = _formula_relation_records(
        region, span_counter, parsed_pages or set(), page_dims or {},
        id_prefix="irel-")
    references = []
    for rec in records:
        if rec["kind"] != "explicit_body_figure_reference":
            continue
        text = ((rec.get("source") or {}).get("text") or "").strip()
        if not text:
            continue
        references.append({
            "text": text,
            "source_span_ids": ([rec["source_span_id"]]
                                if rec.get("source_span_id") else []),
        })
    if asset is None and not caption and not references:
        # Nothing faithful to assemble: the recon already closed such regions
        # as explicit failures; this is a defensive guard only.
        return None
    primary_span = _make_span(span_counter, "primary", pno, bbox)
    section_path = build_section_path(headings, slot.source_index)
    provenance = [_provenance("crop", "image_extraction", "image-1", None)]
    # Consumed plain-text sources (caption dedup traceability): the upstream
    # element id, page and bbox of every text element consumed by this figure.
    consumed_sources = list(slot.extra.get("consumed_sources") or [])
    source_region_ids = [outcome.get("region_id")
                         or region.get("_figure_source_id")
                         or region.get("region_id")]
    processing_config_ids = []
    for cfg in (outcome.get("detection_config_id"),
                region.get("processing_config_id")):
        if isinstance(cfg, str) and cfg and cfg not in processing_config_ids:
            processing_config_ids.append(cfg)
    return {
        "element_id": elem_counter.next(),
        "type": "figure",
        "role": "figure",
        "label": label,
        "section_path": section_path,
        "content": {
            "caption": caption,
            "references": references,
            "asset": asset,
        },
        "source_spans": [primary_span] + rel_spans,
        "provenance": provenance,
        "links": [],
        "quality_status": _STRUCTURED_QUALITY,
        "policy_reasons": list(_STRUCTURED_REASONS),
        "source_region_ids": source_region_ids,
        "processing_config_ids": processing_config_ids,
        "quality_issues": issues,
        "execution_status": "completed",
        "consumed_sources": consumed_sources,
        "crop_assets": [dict(c) for c in crop_assets],
        "cross_page_relations": records,
    }


# --------------------------------------------------------------------------- #
# Source / metadata / generation
# --------------------------------------------------------------------------- #


def _build_source(page_document: dict[str, Any], page_count: int) -> dict[str, Any]:
    sha = (page_document.get("sha256") or "").lower()
    file_name = page_document.get("file_name") or ""
    source_uri = page_document.get("source_uri") or None
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
    page_document: dict[str, Any], override: dict[str, Any] | None
) -> dict[str, Any]:
    source_meta = page_document.get("metadata") or {}
    merged = dict(source_meta)
    if override:
        merged.update(override)

    std_no = (merged.get("standard_number") or "").strip() or None
    file_name = page_document.get("file_name") or "unknown.pdf"
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
        "current", "not_yet_effective", "expired",
        "repealed", "superseded", "draft", "unknown"
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
        # An explicit status needs the date it was determined: a PDF-body
        # declaration is as-of the assembly run (extraction time); there is no
        # external verification event to cite.
        "effective_status_as_of": (
            datetime.now(timezone.utc).date().isoformat()
            if effective_status != "unknown" else None
        ),
        "registry_snapshot_id": registry_snapshot_id,
    }


def _build_generation(
    page_document: dict[str, Any],
    formula_pages: dict[int, list[dict[str, Any]]],
    ocr_engine_order: list[tuple[str, str, str]] | None = None,
    needs_unknown_engine: bool = False,
    formula_engine_order: list[tuple[str, str, str]] | None = None,
    fusion_summary: dict[str, Any] | None = None,
    paragraph_reconstruction: bool = False,
) -> dict[str, Any]:
    config_hash = (page_document.get("config_hash") or "").lower()
    if not config_hash or len(config_hash) != 64:
        # Fall back to a deterministic hash of the file name if config_hash missing.
        config_hash = hashlib.sha256(
            (page_document.get("file_name") or "unknown").encode("utf-8")
        ).hexdigest()
    run_id = page_document.get("processing_run_id") or "v2-assembly-run"
    engines = [
        {"engine_id": "native-1", "name": "pymupdf", "version": "1.24"},
        {"engine_id": "layout-1", "name": "PP-DocLayout_plus-L", "version": "paddleocr-3.7.0"},
        {"engine_id": "image-1", "name": "image-crop", "version": "1.0"},
        {"engine_id": _SECTION_PREFIX_RULE_ENGINE_ID,
         "name": "section-prefix-rendering", "version": _SECTION_PREFIX_RULE_VERSION},
    ]
    # T04-R2: formula engines are REGISTERED from the cache run provenance the
    # document actually consumed (formula_engine_order, built by
    # ``assemble_document``) -- the hardcoded PP-FormulaNet descriptor is gone.
    # A document whose formula cache was produced by a different engine/version
    # therefore registers (and references) THAT engine truthfully.
    for eid, name, version in (formula_engine_order or []):
        engines.append({"engine_id": eid, "name": name, "version": version})
    # T02 P2: register each distinct OCR text engine (name+version) used on any
    # page. Multiple engines are registered separately (ocr-1, ocr-2, ...) so
    # per-page engine_ids resolve (invariant 16 + page-level resolution). A
    # missing engine_version on an old cache is "unknown", never a guess.
    for eid, name, version in (ocr_engine_order or []):
        engines.append({"engine_id": eid, "name": name, "version": version})
    if fusion_summary and fusion_summary.get("mode") in {
            "preprocessed-region-text-v1", "three-source-region-vote-v1"}:
        version = str(fusion_summary.get("run_id") or "unknown")
        if fusion_summary.get("raw_run_id"):
            version += f"+{fusion_summary['raw_run_id']}"
        engines.append({"engine_id": "ocr-region-1", "name": "three-source OCR region vote",
                        "version": version})
    elif fusion_summary and (fusion_summary.get("inserted") or fusion_summary.get("replaced")):
        engines.append({"engine_id": "ocr-fusion-1", "name": "RapidOCR/PP-OCRv6_rec_small",
                        "version": str(fusion_summary.get("run_id") or "unknown")})
    if paragraph_reconstruction:
        engines.append({
            "engine_id": _OCR_PARAGRAPH_RULE_ENGINE_ID,
            "name": "ocr-paragraph-reconstruction",
            "version": _OCR_PARAGRAPH_RULE_VERSION,
        })
    # T02 P1a: a page with a missing/unknown extraction route gets an honest
    # ``unknown-1`` engine (never guessed as native-1) so its provenance
    # engine_id resolves (invariant 16).
    if needs_unknown_engine:
        engines.append({"engine_id": _UNKNOWN_ENGINE_ID, "name": "unknown",
                        "version": "unknown"})
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


def _bbox_containment_ratio(outer: list[float], inner: list[float]) -> float:
    """Fraction of ``inner``'s area that lies inside ``outer`` (0.0..1.0).

    Used for text/table dedup: a text element is consumed only when most of it
    is inside the structured region, so a partial edge overlap does not delete
    an independent paragraph (T03). Returns 0.0 when ``inner`` has no area.
    """
    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner
    iw = ix1 - ix0
    ih = iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0
    cx0, cy0 = max(ox0, ix0), max(oy0, iy0)
    cx1, cy1 = min(ox1, ix1), min(oy1, iy1)
    if cx0 >= cx1 or cy0 >= cy1:
        return 0.0
    return ((cx1 - cx0) * (cy1 - cy0)) / (iw * ih)


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


# --------------------------------------------------------------------------- #
# T02 route -> provenance / quality derivation
# --------------------------------------------------------------------------- #
#
# Assembly is a pure re-organisation layer: it does not re-run OCR, re-judge
# readability as the *primary* quality verdict, or fabricate quality. It reads
# the page-intermediate ``extraction_route`` / ``decision_status`` /
# ``decision_reasons`` AND the upstream text region's ``quality_status`` /
# ``issues`` / ``region_id`` / ``processing_config_id`` (already decided by
# native_pdf / ocr_pdf / unified_pdf) and projects them losslessly onto each
# element so the chunker's strictest-source-wins policy (T01) sees the real
# verdict instead of a re-derived one.
#
# Quality synthesis (strictest wins; D1: never fake ``passed``):
#   votes = [
#     page decision : quarantine -> quarantined
#                    full_ocr/hybrid -> needs_review (OCR needs human verify)
#                    native+approved -> passed (candidate, needs corroboration)
#     region        : if page has text regions ->
#                        matched region quality_status
#                      else (no region match) -> needs_review (cannot trace,
#                        will not guess passed)
#                    if page has NO text regions (old cache) -> abstain
#     text floor    : unreadable text -> needs_review (only ever downgrades)
#   ]
# ``passed`` survives only when native+approved AND a matched region is passed
# (or no regions exist, legacy fallback) AND the text is answerable. A
# quarantined/needs_review region or page always downgrades. Region issues are
# carried losslessly on ``quality_issues``; the matched ``region_id`` /
# ``processing_config_id`` make the element traceable back to its source region.

_NATIVE_ENGINE_ID = "native-1"
_UNKNOWN_ENGINE_ID = "unknown-1"

# Map upstream region/page quality vocabularies (already passed/needs_review/
# quarantined) onto the element quality vote. Unknown values abstain (None) so
# they never silently upgrade.
_REGION_QUALITY_MAP = {"passed": "passed", "needs_review": "needs_review",
                       "quarantined": "quarantined"}


def _route_provenance(slot: _PageItem) -> tuple[str, str | None, float | None]:
    """Map a page extraction route to (provenance method, engine_id, confidence).

    OCR text must never be labelled ``native_text`` (T02 must-complete #2). A
    missing/unknown route is labelled ``unknown`` (never ``native_text``); its
    engine_id is ``unknown-1`` (resolves into generation.engines). Only an
    explicit ``native`` route yields ``native_text`` / ``native-1``. Confidence
    is the page-level OCR mean confidence; None for native/unknown and when the
    page-intermediate does not carry it (pre-T02 caches -> honest null).
    """
    route = slot.extraction_route
    if route == "full_ocr":
        return "ocr", slot.engine_id, slot.ocr_confidence
    if route == "hybrid":
        return "hybrid", slot.engine_id, slot.ocr_confidence
    if route == "native":
        return "native_text", _NATIVE_ENGINE_ID, None
    # missing / unknown / failed route -> honest "unknown", never native_text.
    return "unknown", slot.engine_id or _UNKNOWN_ENGINE_ID, None


def _strictest_quality(votes: list[str | None]) -> str:
    """Strictest non-None vote; empty/all-None -> needs_review (safe default)."""
    rank = {"passed": 0, "needs_review": 1, "quarantined": 2}
    best: str | None = None
    for v in votes:
        if v not in rank:
            continue
        if best is None or rank[v] > rank[best]:
            best = v
    return best if best is not None else "needs_review"


def _derive_text_quality(slot: _PageItem, text: str) -> tuple[str, list[str]]:
    """Derive (quality_status, policy_reasons) for a text element.

    Strictest-wins over page decision, upstream region quality, and the text
    readability floor. ``passed`` requires an EXPLICIT ``native`` route AND
    ``approved`` decision AND (a matched passed region, or no regions on the
    page) AND answerable text -- never a guess. A missing/unknown route or
    decision votes ``needs_review`` (``unknown_source``); the element is never
    promoted to ``passed``. When the page carries text regions but the element
    matched none, it votes ``needs_review`` (cannot trace -> cannot assert
    passed). When it matched several, the strictest region quality wins
    (order-independent).
    """
    reasons: list[str] = []
    votes: list[str | None] = []

    # 1. Page decision / route vote.
    route = slot.extraction_route
    decision = slot.decision_status
    if decision == "quarantine":
        votes.append("quarantined")
        reasons.append("quarantined_source")
        reasons.extend(slot.decision_reasons or [])
    elif decision == "failed":
        votes.append("quarantined")
        reasons.append("failed_extraction_source")
    elif route in ("full_ocr", "hybrid", "scanned_with_text_layer"):
        votes.append("needs_review")
        reasons.append("ocr_source_review")
    elif route == "native" and decision == "approved":
        votes.append("passed")  # the ONLY path that can produce `passed`
    else:
        # missing/unknown route or decision -> conservative needs_review.
        votes.append("needs_review")
        reasons.append("unknown_source")

    # 2. Upstream region vote (P1a: read each region's real quality; P1b:
    #    strictest over ALL matched regions, order-independent). When the page
    #    has text regions but this element matched none, vote needs_review.
    if slot.has_text_regions:
        if slot.region_qualities:
            region_votes = [
                _REGION_QUALITY_MAP[q] for q in slot.region_qualities
                if q in _REGION_QUALITY_MAP
            ]
            if region_votes:
                rq = _strictest_quality(region_votes)
                votes.append(rq)
                if rq != "passed":
                    reasons.append(f"region_quality_{rq}")
            else:
                # all matched regions have unknown quality -> cannot assert passed
                votes.append("needs_review")
                reasons.append("unknown_region_quality")
        else:
            votes.append("needs_review")
            reasons.append("unmatched_text_region")
    # Legacy page-intermediate without text regions: abstain (page decision
    # stands as the quality source).

    # 3. Text readability floor (only ever downgrades).
    if not is_answerable_text(text):
        votes.append("needs_review")
        reasons.extend(score_text(text)["reasons"])

    quality = _strictest_quality(votes)
    # De-duplicate reasons, stable first-seen order.
    seen: set[str] = set()
    deduped: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return quality, deduped


# Structured elements default to needs_review / locator-only (D3). Carrying an
# explicit ``quality_status`` (rather than leaving it absent) makes the D3
# default auditable and prevents the chunker from treating a missing status as
# a content-floor pass.
_STRUCTURED_QUALITY = "needs_review"
_STRUCTURED_REASONS = ["structured_default_locator_only"]


def _parse_status(page_row: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    route = (page_row.get("extraction_route") or "").strip()
    status = (page_row.get("decision_status") or "").strip()
    if route == "failed" or status == "failed":
        return "failed", {"code": "extraction_failed", "message": "V1 extraction route failed"}
    text = (page_row.get("text") or "").strip()
    has_elements = bool(page_row.get("elements"))
    # Pages carrying detected regions (table / figure / formula) are NOT blank:
    # their structured content must reach assembly (T03: no silent omission).
    has_regions = bool(page_row.get("regions"))
    if not text and not has_elements and not has_regions:
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


def _heading_candidate_text(
    text: str, *, bbox: list[float] | None = None, page_width: float = 0.0
) -> str | None:
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
    # OCR often removes the visual gap after a chapter number ("4地块…").
    # Admit that shape here; the hierarchy detector still rejects body-like
    # numbered lines by punctuation/normative-language rules.
    bare = re.match(r"^(\d+)(?=\s|[一-鿿]|$)\s*", norm)
    if bare:
        # In this corpus real bare-number chapter titles are centred; left
        # aligned bare numbers are paragraph/list items.  This geometry gate
        # prevents short fragments such as "1 层或立放" from becoming roots
        # even when they contain no normative verb or terminal punctuation.
        if (bbox is not None and page_width > 0
                and bbox[0] < 0.25 * page_width):
            return None
        rest = norm[bare.end():].strip()
        if (
            rest
            and 2 <= len(rest) <= 15
            and _CHINESE_RE.search(rest)
            and not _PAGE_NUM_RE.match(raw)
        ):
            return norm
    return None


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
