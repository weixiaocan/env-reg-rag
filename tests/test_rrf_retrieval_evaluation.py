import unittest

from src.evaluation.dense_retrieval import evaluate_rrf_retrieval
from src.retrieval.qdrant_index import HybridQuery, RetrievalHit


class _FakeEmbedder:
    model_id = "test/dense"
    model_revision = "test-revision"
    dimension = 2
    query_instruction = "test"

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class _RecordingIndex:
    def __init__(self):
        self.query = None
        self.mode = None

    def build(self, chunks, vectors):
        self.chunks = chunks
        self.vectors = vectors

    def search(self, query, *, mode, filters, limit):
        self.query = query
        self.mode = mode
        return [
            RetrievalHit(
                chunk_id="chunk_00000000000000000000000000000014",
                text="表1：12h降雨量，大雨15.0~29.9毫米。",
                primary_evidence_id="ev_00000000000000000000000000000014",
                physical_pages=[4],
                usage_policy="answer_and_citation",
                score=1.0,
            )
        ]


class RrfRetrievalEvaluationTest(unittest.TestCase):
    def test_report_uses_text_and_dense_vector_for_rrf(self):
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000014",
                "text": "表1：12h降雨量，大雨15.0~29.9毫米。",
            }
        ]
        cases = [
            {
                "case_id": "KNOWN-RRF-001",
                "question": "12小时降水量为20毫米时属于什么等级？",
                "expected_outcome": "retrieve",
                "required_filters": {},
                "required_evidence_ids": ["ev_00000000000000000000000000000014"],
                "acceptable_evidence_ids": [],
            }
        ]
        index = _RecordingIndex()

        report = evaluate_rrf_retrieval(
            chunks, cases, _FakeEmbedder(), index, limit=5
        )

        self.assertEqual(index.mode, "rrf")
        self.assertIsInstance(index.query, HybridQuery)
        self.assertEqual(index.query.text, cases[0]["question"])
        self.assertEqual(report["retrieval_mode"], "rrf")
        self.assertEqual(report["summary"]["recall_at_1"], 1.0)


if __name__ == "__main__":
    unittest.main()
