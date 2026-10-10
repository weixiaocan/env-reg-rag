"""Region-first middle layer for Canonical V2 assembly.

This module converts page-intermediate regions into synthetic text blocks for
the existing faithful Canonical envelope/structured-element assembler.  It is
pure and never writes artifacts.  The old line-level inference remains behind
the legacy mode; v3 uses PP region labels as the authoritative block type.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable

from src.ingestion.column_detection import detect_columns
from src.ingestion.independent_ocr_fusion import (
    load_independent_ocr_page,
    load_independent_ocr_run,
)
from src.ingestion.ocr_region_text import assemble_page_region_text
from src.ingestion.page_furniture import (
    classify_page_furniture,
    normalize_line,
    recurring_band_texts,
)


_CONFIG = Path(__file__).resolve().parents[2] / "config" / "middle_layer_v3.json"
_CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+){1,2})(?=\s|[一-鿿])")
_DOTTED = re.compile(r"^\s*(\d+(?:\.\d+){0,2})(?:[.．])?\s*(.*)$")
_CHAPTER = re.compile(r"^(第[一二三四五六七八九十百千零〇两\d]+章)\s*(.*)$")
_CHINESE_ONE = re.compile(r"^([一二三四五六七八九十]+、)\s*(.*)$")
_CHINESE_PAREN = re.compile(r"^([（(][一二三四五六七八九十]+[）)])\s*(.*)$")
_ROMAN = re.compile(r"^([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[、.．\s]*(.*)$")
_STRUCTURED = {"table", "formula", "formula_number", "figure_title", "image", "chart"}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or _CONFIG).read_text(encoding="utf-8"))


def map_model_label(label: str, config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    key = (label or "text").strip().lower()
    mapping = cfg.get("label_map") or {}
    if key.startswith("reference"):
        return "reference"
    return str(mapping.get(key) or "body")


def split_clause_lines(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split only at continuous dotted clause starts; keep numeric prose intact."""
    starts: list[tuple[int, tuple[int, ...]]] = []
    for index, line in enumerate(lines):
        match = _CLAUSE.match(str(line.get("text") or ""))
        if match:
            starts.append((index, tuple(int(value) for value in match.group(1).split("."))))
    if not starts:
        return [lines] if lines else []
    # A single leading clause still gets clause semantics, but there is no cut.
    if len(starts) == 1:
        return [lines]
    accepted = [starts[0]]
    for candidate in starts[1:]:
        previous = accepted[-1][1]
        current = candidate[1]
        if len(previous) == len(current) and previous[:-1] == current[:-1] and current[-1] == previous[-1] + 1:
            accepted.append(candidate)
        else:
            return [lines]
    boundaries = [index for index, _ in accepted]
    if boundaries[0] != 0:
        boundaries.insert(0, 0)
    boundaries.append(len(lines))
    return [lines[start:end] for start, end in zip(boundaries, boundaries[1:]) if start < end]


def heading_descriptor(text: str, *, is_title: bool = False, is_clause: bool = False) -> dict[str, Any] | None:
    """Return explicit v3 heading metadata; body blocks never call this."""
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    if not first:
        return None
    match = _CHAPTER.match(first)
    if match:
        return _heading(match.group(1), match.group(2), 1)
    match = _DOTTED.match(first)
    if match and (is_title or is_clause):
        label = match.group(1)
        title = None if is_clause else match.group(2)
        return _heading(label, title, label.count(".") + 1)
    if is_title:
        for pattern, level in ((_CHINESE_ONE, 1), (_CHINESE_PAREN, 2), (_ROMAN, 3)):
            match = pattern.match(first)
            if match:
                return _heading(match.group(1), match.group(2), level)
    return None


def prepare_page_document_v3(
    page_document: dict[str, Any], *, independent_ocr_run_dir: str | None = None,
    raw_independent_ocr_run_dir: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deep-copied page document whose text elements are region blocks."""
    cfg = config or load_config()
    doc = deepcopy(page_document)
    ocr_summary = None
    if independent_ocr_run_dir is not None:
        if raw_independent_ocr_run_dir is None:
            raise ValueError("middle-layer v3 requires both clean and raw independent OCR runs")
        manifest, manifest_sha = load_independent_ocr_run(independent_ocr_run_dir)
        raw_manifest, raw_manifest_sha = load_independent_ocr_run(raw_independent_ocr_run_dir)
        preprocessing = dict((manifest.get("config") or {}).get("preprocessing") or {})
        if not preprocessing.get("enabled"):
            raise ValueError("middle-layer v3 requires a preprocessed independent OCR run")
        raw_preprocessing = dict((raw_manifest.get("config") or {}).get("preprocessing") or {})
        if raw_preprocessing.get("enabled"):
            raise ValueError("middle-layer v3 raw independent OCR run must be unpreprocessed")
        run_id = str(manifest["run_id"])
        raw_run_id = str(raw_manifest["run_id"])
        source_sha = str(doc.get("sha256") or "").lower()
        totals = {
            "majority_lines": 0, "merge_lines": 0, "single_source_lines": 0,
            "single_source_pp_clean_lines": 0, "conflict_fragment_count": 0,
            "merge_invariant_violation_count": 0,
            "noise_lines": 0, "orphan_line_groups": 0,
            "number_prefixes_preserved": 0,
        }
        ocr_pages = 0
        for page_index, page in enumerate(doc.get("pages") or []):
            if page.get("extraction_route") not in {"full_ocr", "hybrid"}:
                continue
            physical_page = int(page.get("physical_page") or page_index + 1)
            independent_page = load_independent_ocr_page(
                independent_ocr_run_dir, source_sha, physical_page,
                page_width=float(page.get("width") or 0.0),
                page_height=float(page.get("height") or 0.0),
            )
            raw_page = load_independent_ocr_page(
                raw_independent_ocr_run_dir, source_sha, physical_page,
                page_width=float(page.get("width") or 0.0),
                page_height=float(page.get("height") or 0.0),
            )
            assembled, page_stats = assemble_page_region_text(
                page, independent_page, raw_page, run_id=run_id,
                run_manifest_sha256=manifest_sha, preprocessing=preprocessing,
                raw_run_id=raw_run_id, raw_run_manifest_sha256=raw_manifest_sha,
                watermarks=list(cfg.get("watermark_exact_lines") or []),
                bbox_tolerance=float(cfg.get("ocr_region_bbox_tolerance") or 2.0),
                single_char_score=float(cfg.get("ocr_single_char_min_score") or 0.85),
            )
            doc["pages"][page_index] = assembled
            ocr_pages += 1
            for key in totals:
                totals[key] += page_stats[key]
        ocr_summary = {
            "mode": "three-source-region-vote-v1", "run_id": run_id,
            "run_manifest_sha256": manifest_sha, "preprocessing": preprocessing,
            "raw_run_id": raw_run_id,
            "raw_run_manifest_sha256": raw_manifest_sha,
            "ocr_page_count": ocr_pages, **totals,
        }
    recurring = recurring_band_texts(doc.get("pages") or [])
    stats = {"region_pages": 0, "fallback_pages": 0, "furniture": 0, "toc": 0}
    for page_index, page in enumerate(doc.get("pages") or []):
        blocks = build_page_blocks(page, cfg, recurring=recurring)
        if page.get("regions"):
            stats["region_pages"] += 1
        else:
            stats["fallback_pages"] += 1
        stats["furniture"] += sum(bool(block.get("_v3_furniture_rule")) for block in blocks)
        stats["toc"] += sum(block.get("_v3_role") == "toc" for block in blocks)
        for index, block in enumerate(blocks):
            block["element_id"] = f"p{page_index:04d}-v3-{index:04d}"
            block["page_index"] = page_index
            block["reading_order"] = index
        page["elements"] = blocks
    doc["_middle_layer_v3"] = True
    doc["_v3_stats"] = stats
    if ocr_summary is not None:
        doc["_v3_ocr_summary"] = ocr_summary
    return doc


def build_page_blocks(
    page: dict[str, Any], config: dict[str, Any] | None = None, *,
    recurring: dict[tuple[str, str], set[int]] | None = None,
) -> list[dict[str, Any]]:
    cfg = config or load_config()
    regions = list(page.get("regions") or [])
    if not regions:
        raw_groups = _fallback_groups(page.get("elements") or [])
        candidates = [("text", None, group) for group in raw_groups]
    else:
        candidates = []
        for region in regions:
            label = str(region.get("model_label") or region.get("kind") or "text").lower()
            if label in _STRUCTURED or map_model_label(label, cfg) == "structured":
                continue
            lines = _lines_in_region(
                page.get("elements") or [], region.get("bbox"),
                region_id=str(region.get("region_id") or ""))
            if not lines and not page.get("_v3_ocr_region_text"):
                text = str(region.get("normalized_text") or region.get("text") or "").strip()
                if text:
                    lines = [{"text": value, "bbox": list(region.get("bbox") or [0, 0, 0, 0])}
                             for value in text.splitlines() if value.strip()]
            candidates.append((label, region, lines))

    blocks: list[dict[str, Any]] = []
    for label, region, lines in candidates:
        if not lines:
            continue
        # Exact watermark lines embedded in an otherwise valid text region are
        # separated and marked, never allowed to contaminate the body block.
        body_lines: list[dict[str, Any]] = []
        for line in lines:
            match = classify_page_furniture(
                model_label="text", text=str(line.get("text") or ""),
                bbox=_bbox(line.get("bbox")), page_height=float(page.get("height") or 0),
                watermarks=cfg.get("watermark_exact_lines") or [], recurring=recurring,
                physical_page=int(page.get("physical_page") or 0),
            )
            if match and match.role == "watermark":
                blocks.append(_make_block([line], label, region, role=match.role,
                                          furniture_rule=match.rule_id))
            else:
                body_lines.append(line)
        if not body_lines:
            continue
        mapped = map_model_label(label, cfg)
        body_lines = _drop_clause_label_echoes(body_lines)
        whole_text = _join_lines(body_lines)
        whole_bbox = _union_bbox(body_lines)
        furniture = classify_page_furniture(
            model_label=label, text=whole_text, bbox=whole_bbox,
            page_height=float(page.get("height") or 0),
            watermarks=cfg.get("watermark_exact_lines") or [], recurring=recurring,
            physical_page=int(page.get("physical_page") or 0),
        )
        if furniture:
            blocks.append(_make_block(body_lines, label, region, role=furniture.role,
                                      furniture_rule=furniture.rule_id))
            continue
        if mapped == "toc":
            blocks.append(_make_block(body_lines, label, region, role="toc"))
            continue
        is_title = mapped == "title"
        # A paragraph_title that begins with a clause number and carries body
        # lines is a clause, not a giant heading.  Only its first line can
        # participate in the section hierarchy.
        first_text = str(body_lines[0].get("text") or "").strip()
        clause_match = _CLAUSE.match(first_text)
        clause_like = bool(clause_match and clause_match.group(1).count(".") >= 2)
        groups = split_clause_lines(body_lines) if (mapped == "body" or clause_like) else [body_lines]
        for group in groups:
            text = _join_lines(group)
            clause = bool(_CLAUSE.match(text)) and (mapped == "body" or clause_like)
            role = "clause" if clause else ("heading" if is_title else "paragraph")
            descriptor = heading_descriptor(text, is_title=is_title, is_clause=clause)
            blocks.append(_make_block(group, label, region, role=role,
                                      heading=descriptor))

    layout = detect_columns([_bbox(block["bbox"]) for block in blocks],
                            page_width=float(page.get("width") or 0),
                            page_height=float(page.get("height") or 0))
    ordered = [blocks[index] for index in layout.reading_order]
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[float, ...]]] = set()
    for block in ordered:
        key = (str(block.get("_v3_role")), normalize_line(str(block.get("text") or "")),
               tuple(round(value, 2) for value in _bbox(block.get("bbox"))))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def _make_block(lines: list[dict[str, Any]], label: str, region: dict[str, Any] | None,
                *, role: str, furniture_rule: str | None = None,
                heading: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _join_lines(lines)
    text_sources = [line.get("_ocr_text_source") for line in lines if line.get("_ocr_text_source")]
    result = {
        "type": "text", "text": text, "normalized_text": text,
        "bbox": _union_bbox(lines), "coordinate_origin": "top_left",
        "heading_path": [], "clause_path": [], "parent_id": None,
        "children_ids": [], "confidence": None, "source_block_id": None,
        "_v3_block": True, "_v3_model_label": label, "_v3_role": role,
        "_v3_source_line_bboxes": [_bbox(line.get("bbox")) for line in lines],
        "_v3_region_id": (region or {}).get("region_id"),
    }
    if furniture_rule:
        result["_v3_furniture_rule"] = furniture_rule
    if heading:
        result["_v3_heading"] = heading
    if text_sources:
        result["_v3_ocr_text_sources"] = text_sources
    return result


def _fallback_groups(elements: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    lines = [deepcopy(item) for item in elements if str(item.get("text") or "").strip() and item.get("bbox")]
    lines.sort(key=lambda item: (_bbox(item.get("bbox"))[1], _bbox(item.get("bbox"))[0]))
    if not lines:
        return []
    heights = [max(1.0, _bbox(item["bbox"])[3] - _bbox(item["bbox"])[1]) for item in lines]
    gap_limit = 1.8 * median(heights)
    groups: list[list[dict[str, Any]]] = [[lines[0]]]
    for line in lines[1:]:
        prev = groups[-1][-1]
        gap = _bbox(line["bbox"])[1] - _bbox(prev["bbox"])[3]
        if gap > gap_limit or (_CLAUSE.match(str(line.get("text") or "")) and groups[-1]):
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def _lines_in_region(
    elements: Iterable[dict[str, Any]], raw_bbox: Any, *, region_id: str = "",
) -> list[dict[str, Any]]:
    region = _bbox(raw_bbox)
    selected = []
    for element in elements:
        assigned = str(element.get("_v3_assigned_region_id") or "")
        if assigned and assigned != region_id:
            continue
        bbox = _bbox(element.get("bbox"))
        if bbox and _containment(region, bbox) >= 0.5 and str(element.get("text") or "").strip():
            selected.append(deepcopy(element))
    selected.sort(key=lambda item: (_bbox(item["bbox"])[1], _bbox(item["bbox"])[0], str(item.get("element_id") or "")))
    return selected


def _containment(outer: list[float], inner: list[float]) -> float:
    ix0, iy0, ix1, iy1 = inner
    area = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if area <= 0:
        return 0.0
    x0, y0 = max(outer[0], ix0), max(outer[1], iy0)
    x1, y1 = min(outer[2], ix1), min(outer[3], iy1)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0) / area


def _union_bbox(lines: Iterable[dict[str, Any]]) -> list[float]:
    boxes = [_bbox(line.get("bbox")) for line in lines]
    boxes = [box for box in boxes if box]
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _join_lines(lines: list[dict[str, Any]]) -> str:
    """Join same-baseline OCR fragments horizontally, preserve real rows."""
    values: list[str] = []
    previous_box: list[float] | None = None
    for line in lines:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        box = _bbox(line.get("bbox"))
        if not values:
            values.append(text)
        else:
            prev_height = max(1.0, previous_box[3] - previous_box[1]) if previous_box else 1.0
            height = max(1.0, box[3] - box[1])
            same_baseline = previous_box is not None and abs(
                (previous_box[1] + previous_box[3]) / 2 - (box[1] + box[3]) / 2
            ) <= 0.6 * max(prev_height, height)
            values.append(("" if same_baseline else "\n") + text)
        previous_box = box
    return "".join(values)


def _drop_clause_label_echoes(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not lines:
        return lines
    match = _CLAUSE.match(str(lines[0].get("text") or ""))
    if not match:
        return lines
    label = match.group(1)
    return [lines[0], *[
        line for line in lines[1:]
        if normalize_line(str(line.get("text") or "")) != normalize_line(label)
    ]]


def _bbox(raw: Any) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [float(value) for value in raw]


def _heading(label: str, title: str | None, level: int) -> dict[str, Any]:
    clean = (title or "").strip().rstrip("。.．") or None
    if clean and len(clean) > 60:
        clean = clean[:60]
    return {"label": label.strip(), "title": clean, "level": level}
