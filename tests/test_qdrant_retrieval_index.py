import unittest

from qdrant_client import QdrantClient

from src.retrieval.qdrant_index import QdrantRetrievalIndex


class QdrantRetrievalIndexContractTest(unittest.TestCase):
    def test_exact_dense_returns_the_best_chunk_with_evidence_metadata(self):
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_exact_dense",
            vector_size=2,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000001",
                "text": "24小时降水量20毫米属于中雨。",
                "evidence_ids": ["ev_00000000000000000000000000000001"],
                "primary_evidence_id": "ev_00000000000000000000000000000001",
                "document_version_id": "doc_rainfall",
                "evidence_type": "table",
                "physical_pages": [4],
                "metadata": {
                    "jurisdiction": "全国",
                    "usage_policy": "answer_and_citation",
                },
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000002",
                "text": "排水管道混接调查范围。",
                "evidence_ids": ["ev_00000000000000000000000000000002"],
                "primary_evidence_id": "ev_00000000000000000000000000000002",
                "document_version_id": "doc_cross_connection",
                "evidence_type": "clause",
                "physical_pages": [10],
                "metadata": {
                    "jurisdiction": "全国",
                    "usage_policy": "answer_and_citation",
                },
            },
        ]

        index.build(chunks, [[1.0, 0.0], [0.0, 1.0]])
        hits = index.search([1.0, 0.0], mode="exact_dense", limit=1)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk_id, chunks[0]["chunk_id"])
        self.assertEqual(hits[0].primary_evidence_id, chunks[0]["primary_evidence_id"])
        self.assertEqual(hits[0].physical_pages, [4])
        self.assertEqual(hits[0].usage_policy, "answer_and_citation")
        self.assertAlmostEqual(hits[0].score, 1.0, places=6)

    def test_exact_dense_applies_jurisdiction_filter_before_ranking(self):
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_jurisdiction_filter",
            vector_size=2,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000003",
                "text": "全国排水政策。",
                "evidence_ids": ["ev_00000000000000000000000000000003"],
                "primary_evidence_id": "ev_00000000000000000000000000000003",
                "document_version_id": "doc_national",
                "evidence_type": "paragraph",
                "physical_pages": [4],
                "metadata": {
                    "jurisdiction": "全国",
                    "usage_policy": "answer_and_citation",
                },
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000004",
                "text": "重庆地块项目评价指标。",
                "evidence_ids": ["ev_00000000000000000000000000000004"],
                "primary_evidence_id": "ev_00000000000000000000000000000004",
                "document_version_id": "doc_chongqing",
                "evidence_type": "table",
                "physical_pages": [15],
                "metadata": {
                    "jurisdiction": "重庆",
                    "usage_policy": "answer_and_citation",
                },
            },
        ]
        index.build(chunks, [[1.0, 0.0], [0.8, 0.2]])

        hits = index.search(
            [1.0, 0.0],
            mode="exact_dense",
            filters={"jurisdiction": "重庆"},
            limit=2,
        )

        self.assertEqual([hit.chunk_id for hit in hits], [chunks[1]["chunk_id"]])


if __name__ == "__main__":
    unittest.main()
