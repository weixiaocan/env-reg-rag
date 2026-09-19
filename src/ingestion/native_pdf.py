"""PyMuPDF native text-layer adapter for the M2 parsing baseline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pymupdf


def _native_text_quality(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s", "", text)
    character_count = len(compact)
    replacement_count = compact.count("\ufffd")
    glyph_marker_count = len(re.findall(r"/G\d+", text))
    replacement_ratio = replacement_count / character_count if character_count else 0.0
    reasons = []
    if not character_count:
        reasons.append("empty_text")
    if replacement_count:
        reasons.append("replacement_character_ratio")
    if glyph_marker_count:
        reasons.append("encoded_glyph_markers")
    return {
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
        "metrics": {
            "non_whitespace_characters": character_count,
            "replacement_characters": replacement_count,
            "replacement_character_ratio": round(replacement_ratio, 6),
            "encoded_glyph_markers": glyph_marker_count,
        },
    }


class NativePdfParser:
    """Expose native PDF page extraction in the project's canonical page shape."""

    name = "pymupdf-native"

    def parse_page(self, pdf_path: Path | str, *, physical_page: int) -> dict[str, Any]:
        source = Path(pdf_path)
        if physical_page < 1:
            raise ValueError("physical_page must be a positive 1-based page number")

        with pymupdf.open(source) as document:
            if physical_page > document.page_count:
                raise ValueError(
                    f"physical_page {physical_page} exceeds document page count "
                    f"{document.page_count}"
                )
            page_index = physical_page - 1
            page = document.load_page(page_index)
            text_flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_IMAGES
            block_flags = pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_IMAGES
            raw_flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
            text = page.get_text("text", flags=text_flags)
            native_blocks = page.get_text("blocks", flags=block_flags)
            raw_dict = page.get_text("rawdict", flags=raw_flags)
            rectangle = page.rect
            rotation = page.rotation
            rotation_matrix = page.rotation_matrix

        raw_blocks = []
        elements = []
        for block in native_blocks:
            x0, y0, x1, y1, block_text, block_no, block_type = block[:7]
            if rotation:
                transformed = pymupdf.Rect(x0, y0, x1, y1) * rotation_matrix
                x0, y0, x1, y1 = transformed
            raw_block = {
                "bbox": [float(x0), float(y0), float(x1), float(y1)],
                "text": block_text,
                "block_no": int(block_no),
                "block_type": int(block_type),
            }
            raw_blocks.append(raw_block)
            if block_type != 0 or not block_text.strip():
                continue
            reading_order = len(elements)
            elements.append(
                {
                    "element_id": f"p{page_index:04d}-b{int(block_no):04d}",
                    "type": "text",
                    "text": block_text,
                    "normalized_text": re.sub(r"\s+", " ", block_text).strip(),
                    "page_index": page_index,
                    "bbox": raw_block["bbox"],
                    "coordinate_origin": "top_left",
                    "reading_order": reading_order,
                    "heading_path": [],
                    "clause_path": [],
                    "parent_id": None,
                    "children_ids": [],
                    "confidence": None,
                }
            )

        return {
            "physical_page": physical_page,
            "page_index": page_index,
            "display_page_label": None,
            "width": float(rectangle.width),
            "height": float(rectangle.height),
            "rotation": int(rotation),
            "extraction_route": "native",
            "coordinate_origin": "top_left",
            "coordinate_normalizations": ([{'kind': 'pdf_rotation_matrix', 'rotation': int(rotation),
                                             'matrix': list(rotation_matrix)}] if rotation else []),
            "text": text,
            "quality": _native_text_quality(text),
            "elements": elements,
            "raw": {
                "text": text,
                "blocks": raw_blocks,
                "raw_dict": raw_dict,
            },
        }
