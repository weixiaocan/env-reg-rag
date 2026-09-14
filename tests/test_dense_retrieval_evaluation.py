import unittest

from qdrant_client import QdrantClient

from src.evaluation.dense_retrieval import evaluate_dense_retrieval
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex


class DenseRetrievalEvaluationIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.embedder = BgeSmallZhEmbedder(local_files_only=True)
        except OSError as exc:
            raise unittest.SkipTest(f"cached BGE model is unavailable: {exc}") from exc

    def test_report_scores_a_known_relevant_evidence_from_public_results(self):
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000005",
                "text": "24小时降水量20毫米属于中雨。",
                "evidence_ids": ["ev_00000000000000000000000000000005"],
                "primary_evidence_id": "ev_00000000000000000000000000000005",
                "document_version_id": "doc_rainfall",
                "evidence_type": "table",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000006",
                "text": "排水管道混接调查范围。",
                "evidence_ids": ["ev_00000000000000000000000000000006"],
                "primary_evidence_id": "ev_00000000000000000000000000000006",
                "document_version_id": "doc_cross_connection",
                "evidence_type": "clause",
                "physical_pages": [10],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
        ]
        cases = [
            {
                "case_id": "KNOWN-001",
                "question": "24小时降水量20毫米属于什么等级？",
                "expected_outcome": "retrieve",
                "required_filters": {},
                "required_evidence_ids": ["ev_00000000000000000000000000000005"],
                "acceptable_evidence_ids": [],
            }
        ]
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_dense_evaluation",
            vector_size=512,
        )

        report = evaluate_dense_retrieval(chunks, cases, self.embedder, index, limit=2)

        self.assertEqual(report["summary"]["positive_case_count"], 1)
        self.assertEqual(report["summary"]["recall_at_1"], 1.0)
        self.assertEqual(report["summary"]["mrr"], 1.0)
        self.assertEqual(
            report["cases"][0]["rankings"][0]["primary_evidence_id"],
            "ev_00000000000000000000000000000005",
        )

    def test_report_can_evaluate_the_ann_query_path(self):
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000017",
                "text": "排水管道混接调查范围。",
                "primary_evidence_id": "ev_00000000000000000000000000000017",
                "physical_pages": [10],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            }
        ]
        cases = [
            {
                "case_id": "KNOWN-ANN-001",
                "question": "排水管道混接调查范围是什么？",
                "expected_outcome": "retrieve",
                "required_filters": {},
                "required_evidence_ids": ["ev_00000000000000000000000000000017"],
                "acceptable_evidence_ids": [],
            }
        ]
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_ann_evaluation",
            vector_size=512,
        )

        report = evaluate_dense_retrieval(
            chunks, cases, self.embedder, index, mode="ann_dense", limit=1
        )

        self.assertEqual(report["retrieval_mode"], "ann_dense")
        self.assertEqual(report["summary"]["recall_at_1"], 1.0)


if __name__ == "__main__":
    unittest.main()
