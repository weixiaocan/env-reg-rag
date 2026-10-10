"""Assemble OCR-page text from cleaned PP-OCRv6 lines by layout region."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from src.ingestion.page_furniture import normalize_line


_NUMBER_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+){0,2})(?:\s+|(?=[一-鿿])|$)(.*)$")
_STRUCTURED_KINDS = {"table", "formula", "image", "figure", "chart"}


def assemble_page_region_text(
    page: dict[str, Any], independent_page: dict[str, Any], *,
    run_id: str, run_manifest_sha256: str,
    preprocessing: dict[str, Any], watermarks: list[str],
    bbox_tolerance: float = 2.0, single_char_score: float = 0.85,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a page whose text stream is PP-first and region-assigned."""
    result = deepcopy(page)
    regions = deepcopy(page.get("regions") or [])
    text_regions = [r for r in regions if str(r.get("kind") or "text") not in _STRUCTURED_KINDS]
    structured = [r for r in regions if str(r.get("kind") or "") in _STRUCTURED_KINDS]
    watermark_set = {normalize_line(value) for value in watermarks}
    stats = {"pp_clean_lines": 0, "primary_fallback_lines": 0,
             "noise_lines": 0, "orphan_pp_lines": 0, "number_prefixes_preserved": 0}
    pp_output: list[dict[str, Any]] = []
    accepted_pp: list[dict[str, Any]] = []

    for index, raw in enumerate(independent_page.get("lines") or []):
        line = deepcopy(raw)
        text = str(line.get("text") or "").strip()
        score = _score(line.get("score"))
        compact = normalize_line(text)
        if (not compact or compact in watermark_set
                or (len(compact) == 1 and (score is None or score < single_char_score))):
            stats["noise_lines"] += 1
            continue
        bbox = _bbox(line.get("bbox"))
        if bbox is None or any(_center_inside(bbox, r.get("bbox"), 0.0) for r in structured):
            continue
        matches = [r for r in text_regions if _center_inside(bbox, r.get("bbox"), bbox_tolerance)]
        region = min(matches, key=lambda r: _area(_bbox(r.get("bbox")))) if matches else None
        region_id = str((region or {}).get("region_id") or "")
        if not region_id:
            region_id = f"ocr-orphan-{int(page.get('physical_page') or 0):04d}-{index + 1:04d}"
            regions.append({
                "region_id": region_id, "element_id": region_id,
                "kind": "text", "model_label": "text", "bbox": bbox,
                "execution_status": "completed", "quality_status": "needs_review",
                "issues": ["pp_ocr_line_outside_layout_regions"],
                "relations": [], "processing_config_id": f"{run_id}:orphan-line",
            })
            stats["orphan_pp_lines"] += 1
        primary = _best_vertical_match(bbox, page.get("elements") or [])
        preserved = False
        if primary is not None:
            primary_match = _NUMBER_PREFIX.match(str(primary.get("text") or ""))
            pp_match = _NUMBER_PREFIX.match(text)
            if primary_match and (
                not pp_match
                or (not primary_match.group(2).strip()
                    and pp_match.group(1) != primary_match.group(1))
            ):
                remainder = pp_match.group(2) if pp_match else text
                text = f"{primary_match.group(1)} {remainder}"
                preserved = True
                stats["number_prefixes_preserved"] += 1
        source = _source_record(
            "pp_clean", line_id=str(line.get("line_id") or f"l{index + 1:04d}"),
            score=score, run_id=run_id, manifest_sha=run_manifest_sha256,
            preprocessing=preprocessing, primary_prefix_preserved=preserved,
        )
        pp_line = _element_from_line(line, text, region_id, source, page)
        pp_output.append(pp_line)
        accepted_pp.append(pp_line)
        stats["pp_clean_lines"] += 1

    fallback: list[dict[str, Any]] = []
    for index, primary in enumerate(page.get("elements") or []):
        if str(primary.get("type") or "text") != "text" or not str(primary.get("text") or "").strip():
            continue
        bbox = _bbox(primary.get("bbox"))
        if bbox is None or any(_vertical_overlap(bbox, _bbox(pp.get("bbox"))) > 0 for pp in accepted_pp):
            continue
        matches = [r for r in text_regions if _center_inside(bbox, r.get("bbox"), bbox_tolerance)]
        if not matches:
            continue
        region = min(matches, key=lambda r: _area(_bbox(r.get("bbox"))))
        region_id = str(region.get("region_id") or "")
        source = _source_record(
            "primary_fallback", line_id=str(primary.get("element_id") or f"primary-{index + 1}"),
            score=_score(primary.get("confidence")), run_id=run_id,
            manifest_sha=run_manifest_sha256, preprocessing=preprocessing,
            primary_prefix_preserved=False,
        )
        clone = _element_from_line(primary, str(primary.get("text") or "").strip(), region_id, source, page)
        fallback.append(clone)
        stats["primary_fallback_lines"] += 1

    output = pp_output + fallback
    output.sort(key=lambda row: (_bbox(row.get("bbox"))[1], _bbox(row.get("bbox"))[0],
                                 str(row.get("element_id") or "")))
    for index, row in enumerate(output):
        row["reading_order"] = index
    result["elements"] = output
    result["regions"] = regions
    result["_v3_ocr_region_text"] = True
    return result, stats


def _source_record(source: str, *, line_id: str, score: float | None,
                   run_id: str, manifest_sha: str, preprocessing: dict[str, Any],
                   primary_prefix_preserved: bool) -> dict[str, Any]:
    return {
        "source": source, "line_id": line_id, "score": score,
        "run_id": run_id, "run_manifest_sha256": manifest_sha,
        "preprocessing": dict(preprocessing),
        "primary_prefix_preserved": primary_prefix_preserved,
    }


def _element_from_line(raw: dict[str, Any], text: str, region_id: str,
                       source: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    return {
        "element_id": str(raw.get("element_id") or f"ocr-{source['line_id']}"),
        "type": "text", "text": text, "normalized_text": text,
        "page_index": page.get("page_index"), "bbox": list(_bbox(raw.get("bbox"))),
        "coordinate_origin": "top_left", "reading_order": None,
        "heading_path": [], "clause_path": [], "parent_id": None,
        "children_ids": [], "confidence": source["score"], "source_block_id": None,
        "_v3_assigned_region_id": region_id, "_ocr_text_source": source,
    }


def _best_vertical_match(bbox: list[float], primary: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = []
    for row in primary:
        other = _bbox(row.get("bbox"))
        if other is None or not str(row.get("text") or "").strip():
            continue
        vertical = _vertical_overlap(bbox, other)
        horizontal = max(0.0, min(bbox[2], other[2]) - max(bbox[0], other[0]))
        min_width = max(0.1, min(bbox[2] - bbox[0], other[2] - other[0]))
        if vertical > 0 and horizontal / min_width >= 0.1:
            matches.append((vertical, horizontal / min_width, row))
    return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def _vertical_overlap(a: list[float] | None, b: list[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _center_inside(inner: list[float], outer_raw: Any, tolerance: float) -> bool:
    outer = _bbox(outer_raw)
    if outer is None:
        return False
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - tolerance <= cx <= outer[2] + tolerance and outer[1] - tolerance <= cy <= outer[3] + tolerance


def _area(bbox: list[float] | None) -> float:
    if bbox is None:
        return float("inf")
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        result = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    return result if result[0] <= result[2] and result[1] <= result[3] else None


def _score(raw: Any) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
