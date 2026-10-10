"""Single source of truth for middle-layer-v3 page furniture marking.

The classifier is deliberately conservative: fallback rules only fire in the
top/bottom page bands, watermarks require an exact whole-line match, and
recurring headers/footers require the same short text on several pages.
Nothing is deleted; callers retain the text together with the rule id.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


_EXPLICIT = {
    "number": ("page_number", "pp_label:number"),
    "header": ("header", "pp_label:header"),
    "footer": ("footer", "pp_label:footer"),
    "aside_text": ("unknown", "pp_label:aside_text"),
}
_PAGE_NUMBER = re.compile(
    r"^(?:[·•]\s*\d+\s*[·•]|-\s*\d+\s*-|\d+|第\s*\d+\s*页)$"
)


@dataclass(frozen=True)
class FurnitureMatch:
    role: str
    rule_id: str


def normalize_line(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def watermark_line_variants(
    watermarks: Iterable[str], *, minimum_substring_length: int = 2,
) -> set[str]:
    """Return configured watermark phrases and their whole-line fragments."""
    variants: set[str] = set()
    for value in watermarks:
        normalized = normalize_line(value)
        if not normalized:
            continue
        variants.add(normalized)
        for start in range(len(normalized)):
            for end in range(start + minimum_substring_length, len(normalized) + 1):
                variants.add(normalized[start:end])
    return variants


def recurring_band_texts(
    pages: Iterable[dict[str, Any]], *, min_pages: int = 3, band_ratio: float = 0.12
) -> dict[tuple[str, str], set[int]]:
    """Return recurring ``(band, normalized_text) -> pages`` evidence."""
    seen: dict[tuple[str, str], set[int]] = {}
    for index, page in enumerate(pages, 1):
        pno = int(page.get("physical_page") or index)
        height = float(page.get("height") or 0.0)
        if height <= 0:
            continue
        for region in page.get("regions") or []:
            bbox = _bbox(region.get("bbox"))
            text = str(region.get("normalized_text") or region.get("text") or "").strip()
            norm = normalize_line(text)
            if bbox is None or not norm or len(norm) > 80:
                continue
            band = _band(bbox, height, band_ratio)
            if band:
                seen.setdefault((band, norm), set()).add(pno)
    return {key: value for key, value in seen.items() if len(value) >= min_pages}


def classify_page_furniture(
    *,
    model_label: str,
    text: str,
    bbox: list[float],
    page_height: float,
    watermarks: Iterable[str] = (),
    recurring: dict[tuple[str, str], set[int]] | None = None,
    physical_page: int | None = None,
    band_ratio: float = 0.12,
) -> FurnitureMatch | None:
    """Classify one whole block/line without deleting it."""
    label = (model_label or "").strip().lower()
    if label in _EXPLICIT:
        role, rule = _EXPLICIT[label]
        return FurnitureMatch(role, rule)

    stripped = (text or "").strip()
    norm = normalize_line(stripped)
    watermark_norms = watermark_line_variants(watermarks)
    if norm and norm in watermark_norms:
        return FurnitureMatch("watermark", "watermark:exact_line")

    band = _band(bbox, page_height, band_ratio)
    if band and _PAGE_NUMBER.fullmatch(stripped):
        return FurnitureMatch("page_number", f"fallback:{band}_page_number")

    if band and recurring and physical_page is not None:
        pages = recurring.get((band, norm), set())
        if physical_page in pages:
            return FurnitureMatch(band, f"fallback:recurring_{band}")
    return None


def _band(bbox: list[float], page_height: float, ratio: float) -> str | None:
    if page_height <= 0:
        return None
    if bbox[1] <= page_height * ratio:
        return "header"
    if bbox[3] >= page_height * (1.0 - ratio):
        return "footer"
    return None


def _bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        result = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if result[0] > result[2] or result[1] > result[3]:
        return None
    return result
