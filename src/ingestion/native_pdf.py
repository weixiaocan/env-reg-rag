"""PyMuPDF native text-layer adapter for the M2 parsing baseline."""

from __future__ import annotations

from src.ingestion.text_quality import score_text

import re
from pathlib import Path
from typing import Any

import pymupdf


SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.80


def scanned_text_layer_evidence(page: pymupdf.Page) -> dict[str, Any]:
    """Return evidence for a scan image carrying an invisible OCR text layer.

    A full-page decorative/background image is not sufficient: visible vector
    text keeps the page on the native route.  Third-party OCR layers in the M2
    corpus use PDF text rendering mode 3 (invisible text).
    """
    page_area = float(page.rect.get_area())
    coverages = []
    for image in page.get_image_info(xrefs=True):
        bbox = pymupdf.Rect(image.get("bbox") or (0, 0, 0, 0))
        if page.rotation:
            bbox = bbox * page.rotation_matrix
        bbox &= page.rect
        coverages.append(float(bbox.get_area()) / page_area if page_area else 0.0)
    text = page.get_text("text", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    traces = [trace for trace in page.get_texttrace() if trace.get("chars")]
    invisible_only = bool(traces) and all(int(trace.get("type", -1)) == 3 for trace in traces)
    maximum = max(coverages, default=0.0)
    return {
        "matched": bool(text.strip()) and maximum >= SCANNED_IMAGE_COVERAGE_THRESHOLD and invisible_only,
        "maximum_image_coverage": round(maximum, 8),
        "image_count": len(coverages),
        "has_text_layer": bool(text.strip()),
        "invisible_text_layer": invisible_only,
        "threshold": SCANNED_IMAGE_COVERAGE_THRESHOLD,
    }


def scanned_text_layer_pages(
    pdf_path: Path | str, *, physical_pages: set[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Inspect a PDF once and return matching physical pages with evidence."""
    matches: dict[int, dict[str, Any]] = {}
    with pymupdf.open(pdf_path) as document:
        requested = physical_pages or set(range(1, document.page_count + 1))
        for physical_page in sorted(requested):
            if not 1 <= physical_page <= document.page_count:
                continue
            evidence = scanned_text_layer_evidence(document[physical_page - 1])
            if evidence["matched"]:
                matches[physical_page] = evidence
    return matches


def apply_scanned_text_layer_routes(
    page_document: dict[str, Any], pdf_path: Path | str,
) -> dict[int, dict[str, Any]]:
    """Mark legacy page-intermediate rows using the current generic route rule."""
    matches = scanned_text_layer_pages(pdf_path)
    for index, page in enumerate(page_document.get("pages") or []):
        physical_page = int(page.get("physical_page") or index + 1)
        evidence = matches.get(physical_page)
        if evidence is None:
            continue
        page["extraction_route"] = "scanned_with_text_layer"
        page["decision_status"] = "quarantine"
        page["decision_reasons"] = list(dict.fromkeys(
            list(page.get("decision_reasons") or [])
            + ["scanned_with_text_layer_requires_ocr_vote"]
        ))
        page["scanned_text_layer_evidence"] = evidence
    return matches


def _native_text_quality(text: str) -> dict[str, Any]:
    """Score native PDF text; symbol-only mojibake must fail so OCR can run."""
    return score_text(text)



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
            scan_evidence = scanned_text_layer_evidence(page)
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
            "extraction_route": (
                "scanned_with_text_layer" if scan_evidence["matched"] else "native"
            ),
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
                "scanned_text_layer_evidence": scan_evidence,
            },
        }
