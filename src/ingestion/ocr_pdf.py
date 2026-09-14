"""PaddleOCR PP-StructureV3 adapter for page-level OCR fallback."""

from __future__ import annotations

import importlib.metadata
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pymupdf


class StructureOcrEngine(Protocol):
    """External model boundary consumed by the canonical PDF adapter."""

    name: str
    version: str

    def predict(self, image: np.ndarray) -> dict[str, Any]: ...


class _VisibleHtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)


def _visible_text(value: Any) -> str:
    text = "" if value is None else str(value)
    if "<" not in text or ">" not in text:
        return re.sub(r"\s+", " ", text).strip()
    parser = _VisibleHtmlText()
    parser.feed(text)
    parser.close()
    return " ".join(parser.parts)


def _bbox_in_pdf_points(
    bbox: Any,
    *,
    image_width: float,
    image_height: float,
    page_width: float,
    page_height: float,
) -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"OCR block has invalid bbox: {bbox!r}")
    x0, y0, x1, y1 = (float(value) for value in bbox)
    x_scale = page_width / image_width
    y_scale = page_height / image_height
    converted = [x0 * x_scale, y0 * y_scale, x1 * x_scale, y1 * y_scale]
    converted[0] = min(max(converted[0], 0.0), page_width)
    converted[1] = min(max(converted[1], 0.0), page_height)
    converted[2] = min(max(converted[2], 0.0), page_width)
    converted[3] = min(max(converted[3], 0.0), page_height)
    return converted


def _ocr_quality(text: str, result: dict[str, Any]) -> dict[str, Any]:
    ocr = result.get("overall_ocr_res") or {}
    scores = [float(score) for score in ocr.get("rec_scores", [])]
    compact = re.sub(r"\s", "", text)
    reasons = [] if compact else ["empty_ocr_text"]
    if scores and min(scores) < 0.80:
        reasons.append("low_confidence_text")
    if not compact:
        status = "fail"
    elif reasons:
        status = "review"
    else:
        status = "pass"
    return {
        "status": status,
        "reasons": reasons,
        "metrics": {
            "non_whitespace_characters": len(compact),
            "recognized_line_count": len(ocr.get("rec_texts", [])),
            "mean_confidence": round(sum(scores) / len(scores), 6) if scores else None,
            "minimum_confidence": round(min(scores), 6) if scores else None,
            "review_confidence_threshold": 0.8,
        },
    }


class PaddleStructureV3Engine:
    """Lazy wrapper around PaddleOCR so unit tests do not initialize model weights."""

    name = "paddleocr-ppstructurev3"

    def __init__(self, *, device: str = "cpu", table_recognition: bool = True) -> None:
        from paddleocr import PPStructureV3

        self.version = importlib.metadata.version("paddleocr")
        self.table_recognition = table_recognition
        self._pipeline = PPStructureV3(
            device=device,
            enable_mkldnn=False,
            lang="ch",
            ocr_version="PP-OCRv5",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            use_seal_recognition=False,
            use_table_recognition=table_recognition,
            use_formula_recognition=False,
            use_chart_recognition=False,
            use_region_detection=False,
            format_block_content=False,
        )

    def predict(self, image: np.ndarray) -> dict[str, Any]:
        results = self._pipeline.predict(input=image)
        if len(results) != 1:
            raise RuntimeError(f"expected one OCR page result, got {len(results)}")
        payload = results[0].json["res"]
        overall = payload.get("overall_ocr_res") or {}
        return {
            "width": payload["width"],
            "height": payload["height"],
            "parsing_res_list": payload.get("parsing_res_list", []),
            "overall_ocr_res": {
                key: overall.get(key, [])
                for key in ("rec_texts", "rec_scores", "rec_boxes", "rec_polys", "rec_labels")
            },
            "table_res_list": payload.get("table_res_list", []),
            "model_settings": payload.get("model_settings", {}),
        }


class OcrPdfParser:
    """Render one PDF page and map PP-StructureV3 output to canonical page data."""

    name = "paddleocr-ppstructurev3"

    def __init__(
        self,
        *,
        engine: StructureOcrEngine | None = None,
        dpi: int = 200,
        table_recognition: bool = True,
    ) -> None:
        if dpi <= 0:
            raise ValueError("dpi must be positive")
        self.engine = engine
        self.dpi = dpi
        self.table_recognition = table_recognition

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
            rectangle = page.rect
            rotation = page.rotation
            pixmap = page.get_pixmap(dpi=self.dpi, alpha=False, colorspace=pymupdf.csRGB)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )

        if self.engine is None:
            self.engine = PaddleStructureV3Engine(
                table_recognition=self.table_recognition
            )
        engine = self.engine
        result = engine.predict(image)
        image_width = float(result.get("width") or image.shape[1])
        image_height = float(result.get("height") or image.shape[0])
        if image_width <= 0 or image_height <= 0:
            raise ValueError("OCR engine returned invalid image dimensions")

        blocks = sorted(
            result.get("parsing_res_list", []),
            key=lambda item: (
                item.get("block_order") is None,
                item.get("block_order") if item.get("block_order") is not None else 0,
                item.get("block_id") if item.get("block_id") is not None else 0,
            ),
        )
        elements = []
        text_parts = []
        for reading_order, block in enumerate(blocks):
            text = _visible_text(block.get("block_content"))
            if text:
                text_parts.append(text)
            block_id = block.get("block_id")
            elements.append(
                {
                    "element_id": f"p{page_index:04d}-ocr-{reading_order:04d}",
                    "type": block.get("block_label") or "text",
                    "text": text,
                    "normalized_text": re.sub(r"\s+", " ", text).strip(),
                    "page_index": page_index,
                    "bbox": _bbox_in_pdf_points(
                        block.get("block_bbox"),
                        image_width=image_width,
                        image_height=image_height,
                        page_width=float(rectangle.width),
                        page_height=float(rectangle.height),
                    ),
                    "coordinate_origin": "top_left",
                    "reading_order": reading_order,
                    "heading_path": [],
                    "clause_path": [],
                    "parent_id": None,
                    "children_ids": [],
                    "confidence": None,
                    "source_block_id": block_id,
                }
            )

        tables = []
        for table_index, raw_table in enumerate(result.get("table_res_list", [])):
            html = raw_table.get("pred_html", "")
            tables.append(
                {
                    "table_id": f"p{page_index:04d}-table-{table_index:04d}",
                    "page_index": page_index,
                    "text": _visible_text(html),
                    "html": html,
                    "raw": raw_table,
                }
            )

        text = "\n".join(text_parts)
        return {
            "physical_page": physical_page,
            "page_index": page_index,
            "display_page_label": None,
            "width": float(rectangle.width),
            "height": float(rectangle.height),
            "rotation": int(rotation),
            "extraction_route": "full_ocr",
            "coordinate_origin": "top_left",
            "text": text,
            "quality": _ocr_quality(text, result),
            "elements": elements,
            "tables": tables,
            "raw": {
                "engine_name": engine.name,
                "engine_version": engine.version,
                "render_dpi": self.dpi,
                "render_width": image.shape[1],
                "render_height": image.shape[0],
                "engine_result": result,
            },
        }
