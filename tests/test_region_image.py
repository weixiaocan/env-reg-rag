from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pymupdf
from qdrant_client import QdrantClient

from src.adapters.pdf_region_renderer import RegionImageService
from src.adapters.source_evidence_document_catalog import (
    SourceEvidenceDocumentCatalog,
)
from src.retrieval.qdrant_index import QdrantRetrievalIndex


def _write_minimal_pdf(path: Path) -> tuple[float, float, list[float]]:
    """Create a real 1-page PDF with known geometry.

    Returns (page_width, page_height, a_bbox_inside_the_page).
    """
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US Letter points
    page.insert_text((72, 72), "drainage regulation test")
    doc.save(str(path))
    doc.close()
    # bbox around the inserted text (in points).
    return 612, 792, [60, 60, 220, 90]


def _write_source_evidence(root: Path, sha: str, rel_path: str, file_name: str) -> Path:
    registry_path = root / "data" / "registry" / "source_evidence.jsonl"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "file_sha256": sha,
        "aliases": [{"file_name": file_name, "rel_path": rel_path}],
        "observations": [],
    }
    with registry_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return registry_path


class RegionImageServiceTest(unittest.TestCase):
    """Render a PNG crop of a chunk's source_spans against a real PDF."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        pdf_rel = "data/raw/region_test.pdf"
        pdf_path = root / pdf_rel
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        _, _, self.bbox = _write_minimal_pdf(pdf_path)

        self.sha = "a" * 64
        registry_path = _write_source_evidence(root, self.sha, pdf_rel, "region_test.pdf")
        self.catalog = SourceEvidenceDocumentCatalog(
            project_root=root, registry_path=registry_path
        )

        self.client = QdrantClient(url="http://127.0.0.1:6333", timeout=10)
        self.collection = "test_region_image_service"
        self.index = QdrantRetrievalIndex(
            client=self.client,
            collection_name=self.collection,
            vector_size=2,
            enable_bm25=False,
        )
        self.chunk_id = "chunk_regiontest0000000000000000000000a"
        self.page = 1
        chunk = {
            "chunk_id": self.chunk_id,
            "text": "测试原文区域",
            "primary_evidence_id": self.chunk_id,
            "document_version_id": self.sha,
            "physical_pages": [self.page],
            "source_spans": [
                {"physical_page": self.page, "bbox": self.bbox},
            ],
        }
        self.index.build([chunk], [[1.0, 0.0]])
        self.service = RegionImageService(
            index=self.index, document_catalog=self.catalog
        )

    def tearDown(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self._tmp.cleanup()

    def test_renders_png_for_valid_region(self):
        region_id = f"{self.chunk_id}__p{self.page}"
        png = self.service.image(region_id)
        self.assertIsNotNone(png)
        self.assertIsInstance(png, bytes)
        # PNG magic header.
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_unknown_chunk_returns_none(self):
        png = self.service.image("chunk_nonexistent0000000000000000000__p1")
        self.assertIsNone(png)

    def test_missing_page_returns_none(self):
        region_id = f"{self.chunk_id}__p99"
        self.assertIsNone(self.service.image(region_id))

    def test_malformed_region_id_returns_none(self):
        self.assertIsNone(self.service.image("not_a_region_id"))
        self.assertIsNone(self.service.image("chunk_abc__p"))
        self.assertIsNone(self.service.image("chunk_abc__px"))


if __name__ == "__main__":
    unittest.main()
