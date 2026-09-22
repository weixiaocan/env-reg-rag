"""Render source-region crop images from V2 chunk ``source_spans``.

A V2 "region" is the natural per-page projection of a chunk: the chunk's
``source_spans`` on a given physical page, unioned into one bbox and rendered
to PNG via PyMuPDF. The ``region_id`` encodes both chunk id and page
(``{chunk_id}__p{page}``) so this service can re-fetch the chunk from the
retrieval index, locate the spans, and crop the source PDF -- no separate
published-region registry or pre-computed region table is required.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from src.adapters.inventory_document_catalog import DocumentCatalog
from src.retrieval.qdrant_index import QdrantRetrievalIndex

# 200 dpi pixel budget for a single crop (mirrors the V1 published-region guard).
_MAX_CROP_PIXELS = 12_000_000
_DPI = 200


class RegionImageService:
    """Render a PNG crop of a chunk's source spans on one PDF page."""

    def __init__(
        self,
        *,
        index: QdrantRetrievalIndex,
        document_catalog: DocumentCatalog,
    ) -> None:
        self._index = index
        self._catalog = document_catalog

    def image(self, region_id: str) -> bytes | None:
        """Return PNG bytes for the region, or ``None`` if unavailable.

        Returns ``None`` (rather than raising) for every not-found / malformed /
        oversized case so the HTTP route can map uniformly to 404.
        """
        chunk_id, page = _parse_region_id(region_id)
        if chunk_id is None or page is None:
            return None
        hits = self._index.find_by_evidence_ids([chunk_id])
        if not hits:
            return None
        hit = hits[0]
        spans = [
            s
            for s in getattr(hit, "source_spans", [])
            if isinstance(s, dict) and s.get("physical_page") == page
        ]
        bbox = _union_bbox(spans)
        if bbox is None:
            return None
        document = self._catalog.get(hit.document_version_id)
        if document is None or document.local_path is None:
            return None
        return _render_crop(document.local_path, page, bbox)


def _parse_region_id(region_id: str) -> tuple[str | None, int | None]:
    """Split ``{chunk_id}__p{page}`` into (chunk_id, page)."""
    if not isinstance(region_id, str) or "__p" not in region_id:
        return None, None
    head, _, tail = region_id.rpartition("__p")
    if not head or not tail.isdigit():
        return None, None
    page = int(tail)
    if page <= 0:
        return None, None
    return head, page


def _union_bbox(spans: list[dict]) -> list[float] | None:
    if not spans:
        return None
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for span in spans:
        bbox = span.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            sx0, sy0, sx1, sy1 = (float(v) for v in bbox)
        except (TypeError, ValueError):
            continue
        if sx0 > sx1 or sy0 > sy1:
            continue
        x0 = min(x0, sx0)
        y0 = min(y0, sy0)
        x1 = max(x1, sx1)
        y1 = max(y1, sy1)
    if x0 > x1 or y0 > y1:
        return None
    return [x0, y0, x1, y1]


def _render_crop(pdf_path: Path, page: int, bbox: list[float]) -> bytes | None:
    scale = _DPI / 72.0
    width = (bbox[2] - bbox[0]) * scale
    height = (bbox[3] - bbox[1]) * scale
    if width <= 0 or height <= 0 or width * height > _MAX_CROP_PIXELS:
        return None
    try:
        with pymupdf.open(pdf_path) as pdf:
            if page > len(pdf):
                return None
            page_rect = pdf[page - 1].rect
            clip = pymupdf.Rect(bbox)
            if not page_rect.contains(clip) or clip.is_empty:
                return None
            return pdf[page - 1].get_pixmap(
                dpi=_DPI, clip=clip, alpha=False
            ).tobytes("png")
    except Exception:
        return None
