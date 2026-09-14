"""Resource-aware OCR profile selection for representative PDF pages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrRouteDecision:
    profile: str
    mandatory_review_reason: str | None = None


def select_ocr_route(content_type: str) -> OcrRouteDecision:
    """Select the cheapest profile that preserves the page's evidence type."""

    normalized = content_type.strip().lower()
    if "table" in normalized:
        return OcrRouteDecision(profile="table_structure_heavy")
    if normalized == "formula":
        return OcrRouteDecision(
            profile="text_layout_light",
            mandatory_review_reason="formula_structure",
        )
    if "figure" in normalized or normalized == "flowchart":
        return OcrRouteDecision(
            profile="text_layout_light",
            mandatory_review_reason="visual_structure",
        )
    return OcrRouteDecision(profile="text_layout_light")
