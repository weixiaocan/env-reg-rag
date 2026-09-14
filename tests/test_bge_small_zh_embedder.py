import math
import unittest

from src.retrieval.bge_small_zh import BgeSmallZhEmbedder


class BgeSmallZhEmbedderIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.embedder = BgeSmallZhEmbedder(local_files_only=True)
        except OSError as exc:
            raise unittest.SkipTest(f"cached BGE model is unavailable: {exc}") from exc

    def test_query_and_documents_use_normalized_512_dimension_vectors(self):
        query = self.embedder.embed_query("24小时降水量20毫米属于什么等级？")
        documents = self.embedder.embed_documents(
            ["24小时降水量20毫米属于中雨。", "排水管道混接调查范围。"]
        )

        self.assertEqual(len(query), 512)
        self.assertEqual([len(vector) for vector in documents], [512, 512])
        for vector in [query, *documents]:
            self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0, places=5)
        self.assertGreater(
            sum(left * right for left, right in zip(query, documents[0], strict=True)),
            sum(left * right for left, right in zip(query, documents[1], strict=True)),
        )

    def test_document_diagnostics_expose_inputs_that_will_be_truncated(self):
        diagnostics = self.embedder.inspect_documents(
            ["简短条款", "降水量" * 600]
        )

        self.assertEqual(diagnostics["input_count"], 2)
        self.assertEqual(diagnostics["max_length"], 512)
        self.assertEqual(diagnostics["truncated_count"], 1)
        self.assertGreater(diagnostics["max_observed_tokens"], 512)


if __name__ == "__main__":
    unittest.main()
