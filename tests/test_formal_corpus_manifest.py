import unittest

from src.application.formal_corpus import FormalCorpusManifestBuilder


class FormalCorpusManifestBuilderTest(unittest.TestCase):
    def test_only_documents_passing_every_source_gate_enter_the_manifest(self):
        reviews = [
            {
                "file_name": "official-current.pdf",
                "official_source_uri": "https://example.gov.cn/official-current.pdf",
                "source_review": "official_fulltext_verified",
                "effective_status": "current",
                "publication_date": "2024-01-01",
                "effective_date": "2024-02-01",
                "local_file_match": "sha256_match",
                "selection_status": "approved_formal",
            },
            {
                "file_name": "record-only.pdf",
                "official_source_uri": "https://example.gov.cn/record",
                "source_review": "official_record_found",
                "effective_status": "current",
                "local_file_match": "not_compared",
                "selection_status": "approved_experiment",
            },
        ]
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000031",
                "document_version_id": "doc_official",
                "metadata": {"file_name": "official-current.pdf"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000032",
                "document_version_id": "doc_record_only",
                "metadata": {"file_name": "record-only.pdf"},
            },
        ]

        manifest = FormalCorpusManifestBuilder().build(
            review_rows=reviews,
            chunks=chunks,
            corpus_version="corpus-v1",
        )

        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(manifest["chunk_ids"], [chunks[0]["chunk_id"]])
        self.assertEqual(manifest["document_version_ids"], ["doc_official"])
        self.assertEqual(
            manifest["excluded_documents"],
            [
                {
                    "file_name": "record-only.pdf",
                    "reasons": [
                        "official_fulltext_not_verified",
                        "local_file_not_matched",
                        "not_approved_for_formal_release",
                    ],
                }
            ],
        )

        formal_chunks = FormalCorpusManifestBuilder().project_chunks(
            review_rows=reviews,
            chunks=chunks,
        )
        self.assertEqual(len(formal_chunks), 1)
        self.assertEqual(
            formal_chunks[0]["metadata"]["official_source_uri"],
            "https://example.gov.cn/official-current.pdf",
        )
        self.assertEqual(formal_chunks[0]["metadata"]["effective_status"], "current")
        self.assertEqual(formal_chunks[0]["metadata"]["effective_from"], "2024-02-01")
        self.assertEqual(formal_chunks[0]["metadata"]["effective_to"], "9999-12-31")

    def test_manifest_is_blocked_when_no_document_passes_the_gate(self):
        manifest = FormalCorpusManifestBuilder().build(
            review_rows=[
                {
                    "file_name": "experiment-only.pdf",
                    "official_source_uri": "",
                    "source_review": "needs_primary_source",
                    "effective_status": "unknown",
                    "local_file_match": "not_compared",
                    "selection_status": "approved_experiment",
                }
            ],
            chunks=[],
            corpus_version="corpus-v1",
        )

        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["chunk_ids"], [])


if __name__ == "__main__":
    unittest.main()
