"""Independent local OCR evidence for page-intermediate text regions.

This verifier deliberately does not create human assertions.  It records an
automatic pass only for a strict, whitespace-insensitive NFKC text match whose
independently detected lines geometrically belong to the same source region
and whose minimum recognition confidence meets the configured threshold.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


def strict_text(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def mapped_bbox(points: Iterable[Iterable[float]], *, rotation_k: int,
                original_width_px: int, original_height_px: int,
                scale: float) -> list[float]:
    """Map a numpy.rot90 OCR quadrilateral back to page coordinates."""
    restored: list[tuple[float, float]] = []
    for point in points:
        x, y = map(float, point)
        if rotation_k == 0:
            ox, oy = x, y
        elif rotation_k == 1:  # counter-clockwise
            ox, oy = original_width_px - y, x
        elif rotation_k == 2:
            ox, oy = original_width_px - x, original_height_px - y
        elif rotation_k == 3:  # clockwise
            ox, oy = y, original_height_px - x
        else:
            raise ValueError(f"unsupported numpy rotation: {rotation_k}")
        restored.append((ox / scale, oy / scale))
    xs = [p[0] for p in restored]
    ys = [p[1] for p in restored]
    return [min(xs), min(ys), max(xs), max(ys)]


def _intersection(a: list[float], b: list[float]) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def line_belongs_to_region(line_bbox: list[float], region_bbox: list[float]) -> bool:
    cx = (line_bbox[0] + line_bbox[2]) / 2
    cy = (line_bbox[1] + line_bbox[3]) / 2
    if region_bbox[0] <= cx <= region_bbox[2] and region_bbox[1] <= cy <= region_bbox[3]:
        return True
    return _intersection(line_bbox, region_bbox) >= 0.60 * min(_area(line_bbox), _area(region_bbox))


def verify_text_region(region: dict[str, Any], lines: list[dict[str, Any]], *,
                       confidence_threshold: float = 0.95) -> dict[str, Any]:
    bbox = [float(v) for v in region.get("bbox") or []]
    selected = [line for line in lines if len(bbox) == 4 and
                line_belongs_to_region([float(v) for v in line["bbox"]], bbox)]
    selected.sort(key=lambda row: (round(float(row["bbox"][1]), 1),
                                   round(float(row["bbox"][0]), 1)))
    expected = strict_text(region.get("text"))
    independent = strict_text("".join(str(row.get("text") or "") for row in selected))
    chosen = selected
    alignment_mode = "full_geometric_selection"
    if expected and expected != independent:
        exact_subsets: list[list[dict[str, Any]]] = []
        for start in range(len(selected)):
            accumulated = ""
            for end in range(start, len(selected)):
                accumulated += strict_text(selected[end].get("text"))
                if accumulated == expected:
                    exact_subsets.append(selected[start:end + 1])
                    break
                if len(accumulated) > len(expected):
                    break
        if len(exact_subsets) == 1:
            chosen = exact_subsets[0]
            independent = expected
            alignment_mode = "unique_contiguous_exact_subset"
        elif len(exact_subsets) > 1:
            alignment_mode = "ambiguous_contiguous_exact_subsets"
    scores = [float(row.get("score") or 0.0) for row in chosen]
    exact = bool(expected) and expected == independent
    passed = exact and bool(scores) and min(scores) >= confidence_threshold
    return {
        "region_id": region.get("region_id"),
        "automatic_conclusion": "verified_passed" if passed else "manual_review_required",
        "method": "independent_rapidocr_ppocrv6_strict_region_match",
        "strict_text_match": exact,
        "alignment_mode": alignment_mode,
        "confidence_threshold": confidence_threshold,
        "minimum_line_confidence": min(scores) if scores else None,
        "source_text_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "independent_text_sha256": hashlib.sha256(independent.encode("utf-8")).hexdigest(),
        "matched_line_count": len(chosen),
        "matched_line_ids": [str(row.get("line_id")) for row in chosen],
        "verification_authority": "automatic_local_model_comparison",
        "uncertainty": [] if passed else ["independent_ocr_not_an_exact_high_confidence_match"],
    }


def load_region_verifications(path: Path | str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            region_id = str(row.get("region_id") or "")
            if not region_id or region_id in rows:
                raise ValueError(f"missing or duplicate independent OCR region id: {region_id!r}")
            rows[region_id] = row
    return rows


def load_page_evidence(run_dir: Path | str) -> dict[tuple[str, int], dict[str, Any]]:
    run_dir = Path(run_dir)
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    for path in sorted((run_dir / "pages").glob("*/*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        key = (str(row.get("file_sha256") or ""), int(row.get("physical_page") or 0))
        if not key[0] or key[1] < 1 or key in pages:
            raise ValueError(f"invalid or duplicate independent OCR page evidence: {path}")
        pages[key] = row
    return pages
