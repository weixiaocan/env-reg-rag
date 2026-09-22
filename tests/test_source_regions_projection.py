from __future__ import annotations

import unittest

from src.retrieval.qdrant_index import QdrantRetrievalIndex, _derive_source_regions


class _FakePoint:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.id = "fake"


class SourceRegionsProjectionTest(unittest.TestCase):
    """_to_hit must project V2 source_spans into per-page source_regions."""

    def test_single_page_spans_produce_one_region_with_union_bbox(self):
        payload = {
            "chunk_id": "chunk_abc123",
            "text": "某条款原文",
            "source_spans": [
                {"physical_page": 17, "bbox": [10, 20, 100, 40]},
                {"physical_page": 17, "bbox": [10, 45, 80, 70]},
            ],
        }
        hit = QdrantRetrievalIndex._to_hit(_FakePoint(payload), score=1.0)
        self.assertEqual(len(hit.source_regions), 1)
        region = hit.source_regions[0]
        self.assertEqual(region["region_id"], "chunk_abc123__p17")
        self.assertEqual(region["physical_page"], 17)
        self.assertEqual(region["bbox"], [10, 20, 100, 70])  # union
        self.assertEqual(region["kind"], "text")

    def test_cross_page_spans_produce_one_region_per_page(self):
        payload = {
            "chunk_id": "chunk_def456",
            "text": "跨页内容",
            "source_spans": [
                {"physical_page": 17, "bbox": [10, 20, 100, 40]},
                {"physical_page": 18, "bbox": [50, 60, 200, 90]},
            ],
        }
        hit = QdrantRetrievalIndex._to_hit(_FakePoint(payload), score=1.0)
        pages = sorted(r["physical_page"] for r in hit.source_regions)
        self.assertEqual(pages, [17, 18])
        ids = {r["region_id"] for r in hit.source_regions}
        self.assertEqual(ids, {"chunk_def456__p17", "chunk_def456__p18"})

    def test_formula_text_prefix_sniffed_as_formula_kind(self):
        payload = {
            "chunk_id": "chunk_f1",
            "text": "[公式 5.4.21]\nQ=...",
            "source_spans": [{"physical_page": 23, "bbox": [0, 0, 10, 10]}],
        }
        hit = QdrantRetrievalIndex._to_hit(_FakePoint(payload), score=1.0)
        self.assertEqual(hit.source_regions[0]["kind"], "formula")

    def test_no_spans_yields_empty_regions(self):
        payload = {"chunk_id": "chunk_x", "text": "无坐标", "source_spans": []}
        hit = QdrantRetrievalIndex._to_hit(_FakePoint(payload), score=1.0)
        self.assertEqual(hit.source_regions, [])

    def test_malformed_spans_skipped(self):
        payload = {
            "chunk_id": "chunk_y",
            "text": "含坏数据",
            "source_spans": [
                {"physical_page": 5, "bbox": [0, 0, 10, 10]},
                {"physical_page": "bad", "bbox": [0, 0, 1, 1]},
                {"physical_page": 6, "bbox": [1, 2, 3]},  # wrong len
                {"physical_page": 7, "bbox": [5, 5, 1, 1]},  # x0>x1
            ],
        }
        hit = QdrantRetrievalIndex._to_hit(_FakePoint(payload), score=1.0)
        pages = [r["physical_page"] for r in hit.source_regions]
        self.assertEqual(pages, [5])  # only the valid one survives


if __name__ == "__main__":
    unittest.main()
