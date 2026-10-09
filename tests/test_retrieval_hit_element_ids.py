"""Regression test for exposing Canonical element IDs to evaluation callers."""

from types import SimpleNamespace
import unittest

from src.retrieval.qdrant_index import QdrantRetrievalIndex


class RetrievalHitElementIdsTests(unittest.TestCase):
    def test_qdrant_payload_element_ids_are_preserved_on_hit(self) -> None:
        point = SimpleNamespace(
            payload={
                "chunk_id": "chunk-1",
                "text": "证据",
                "primary_evidence_id": "chunk-1",
                "physical_pages": [3],
                "element_ids": ["e001", "e002"],
                "usage_policy": "answer_and_citation",
            }
        )

        hit = QdrantRetrievalIndex._to_hit(point, score=0.9)

        self.assertEqual(hit.element_ids, ["e001", "e002"])


if __name__ == "__main__":
    unittest.main()
