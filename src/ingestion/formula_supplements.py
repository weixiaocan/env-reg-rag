"""Source-bound layout supplements for formula regions missed by the local model.

Only geometry is registered here. Formula text is still read from a local PDF
crop by the normal recognizer, and its content remains unreviewed.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

REGISTRY = Path("data/registry/formula-layout-supplements.json")
SOURCE = "source_page_layout_supplement"


def registry_digest(root: Path) -> str | None:
    path = Path(root) / REGISTRY
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def supplemented_layout_boxes(root: Path, sha256: str, physical_page: int,
                              page_width: float, page_height: float,
                              pixel_width: int, pixel_height: int,
                              detected: list[dict]) -> list[dict]:
    """Append validated PDF-point boxes as pixel boxes, without duplicating detections."""
    path = Path(root) / REGISTRY
    if not path.is_file():
        return list(detected)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1" or not isinstance(data.get("regions"), list):
        raise ValueError("invalid formula layout supplements registry")
    result = list(detected)
    seen: set[tuple[str, int, tuple[float, ...]]] = set()
    for entry in data["regions"]:
        sha, page, bbox = entry.get("source_sha256"), entry.get("physical_page"), entry.get("bbox")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid formula supplement source sha256")
        if type(page) is not int or page < 1 or not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("invalid formula supplement page or bbox")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in bbox):
            raise ValueError("invalid formula supplement bbox coordinate")
        key = (sha, page, tuple(float(v) for v in bbox))
        if key in seen:
            raise ValueError("duplicate formula layout supplement")
        seen.add(key)
        if sha != sha256 or page != physical_page:
            continue
        x0, y0, x1, y1 = bbox
        if not (0 <= x0 < x1 <= page_width and 0 <= y0 < y1 <= page_height):
            raise ValueError("formula layout supplement outside source page")
        pixel_box = [x0 * pixel_width / page_width, y0 * pixel_height / page_height,
                     x1 * pixel_width / page_width, y1 * pixel_height / page_height]
        for prior in result:
            if prior.get("label") != "formula":
                continue
            b = prior.get("coordinate") or []
            if len(b) != 4:
                continue
            intersection = max(0, min(b[2], pixel_box[2]) - max(b[0], pixel_box[0])) * max(
                0, min(b[3], pixel_box[3]) - max(b[1], pixel_box[1]))
            area = (pixel_box[2] - pixel_box[0]) * (pixel_box[3] - pixel_box[1])
            if intersection / area > .5:
                raise ValueError("formula supplement overlaps a detected formula")
        result.append({"label": "formula", "coordinate": pixel_box, "score": None,
                       "detection_source": SOURCE, "source_bbox": bbox,
                       "review_status": "needs_review"})
    return result
