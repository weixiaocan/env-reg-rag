import unittest

from qdrant_client import QdrantClient

from src.evaluation.dense_retrieval import evaluate_bm25_retrieval
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex


class Bm25RetrievalEvaluationServiceTest(unittest.TestCase):
    collection_name = "test_m3_bm25_evaluation_service"

    @classmethod
    def setUpClass(cls):
        try:
            cls.embedder = BgeSmallZhEmbedder(local_files_only=True)
        except OSError as exc:
            raise unittest.SkipTest(f"cached BGE model is unavailable: {exc}") from exc

    def test_report_scores_multilingual_bm25_from_service_results(self):
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=self.collection_name,
            vector_size=512,
            enable_bm25=True,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000011",
                "text": "降水量按24h、12h两个时间段进行划分。",
                "evidence_ids": ["ev_00000000000000000000000000000011"],
                "primary_evidence_id": "ev_00000000000000000000000000000011",
                "document_version_id": "doc_rainfall",
                "evidence_type": "paragraph",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000012",
                "text": "表1：12h降雨量，大雨15.0~29.9毫米；24h降雨量，中雨10.0~24.9毫米。",
                "evidence_ids": ["ev_00000000000000000000000000000012"],
                "primary_evidence_id": "ev_00000000000000000000000000000012",
                "document_version_id": "doc_rainfall",
                "evidence_type": "table",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
        ]
        cases = [
            {
                "case_id": "KNOWN-BM25-001",
                "question": "12h降雨量20毫米属于什么等级",
                "expected_outcome": "retrieve",
                "required_filters": {"jurisdiction": "全国"},
                "required_evidence_ids": ["ev_00000000000000000000000000000012"],
                "acceptable_evidence_ids": [],
            }
        ]
        try:
            report = evaluate_bm25_retrieval(
                chunks, cases, self.embedder, index, limit=2
            )

            self.assertEqual(report["retrieval_mode"], "bm25")
            self.assertEqual(report["summary"]["recall_at_1"], 1.0)
            self.assertEqual(report["summary"]["mrr"], 1.0)
        finally:
            if client.collection_exists(self.collection_name):
                client.delete_collection(self.collection_name)


if __name__ == "__main__":
    unittest.main()
