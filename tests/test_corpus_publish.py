from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client import models

from src.application.corpus_publish import publish_corpus_candidate


class FakeEmbedder:
    dimension = 3
    model_id = "fake-embedding-v1"

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def embed_documents(self, texts):
        self.embedded.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


class CorpusPublishTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "registry").mkdir(parents=True)
        (self.root / "data" / "retrieval").mkdir(parents=True)
        self.client = QdrantClient(":memory:")

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def write_candidate(self, *, status: str = "ready") -> None:
        version = "corpus-abc123"
        chunk = {
            "schema_version": "1",
            "chunk_id": "chunk_00000000000000000000000000000001",
            "text": "标准号：GB 1-2026\n原文：测试规范内容",
            "evidence_ids": ["ev_00000000000000000000000000000001"],
            "primary_evidence_id": "ev_00000000000000000000000000000001",
            "document_version_id": "doc_0000000000000001",
            "evidence_type": "paragraph",
            "physical_pages": [1],
            "metadata": {
                "file_name": "测试规范.pdf",
                "usage_policy": "answer_and_citation",
                "quality_status": "approved",
            },
        }
        chunks = self.root / "data" / "retrieval" / f"{version}-chunks.jsonl"
        chunks.write_text(json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": "1",
            "corpus_version": version,
            "status": status,
            "chunk_count": 1,
            "retrieval_chunks": chunks.relative_to(self.root).as_posix(),
            "retrieval_chunks_sha256": hashlib.sha256(chunks.read_bytes()).hexdigest(),
        }
        manifest_path = self.root / "data" / "registry" / f"{version}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        pointer = {
            "corpus_version": version,
            "status": status,
            "manifest": manifest_path.relative_to(self.root).as_posix(),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        (self.root / "data" / "registry" / "corpus-candidate.json").write_text(
            json.dumps(pointer), encoding="utf-8"
        )

    def test_ready_candidate_is_published_through_stable_alias(self):
        self.write_candidate()
        embedder = FakeEmbedder()

        result = publish_corpus_candidate(
            self.root,
            client=self.client,
            embedder=embedder,
            enable_bm25=False,
        )

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["collection_name"], "corpus_abc123")
        self.assertEqual(embedder.embedded, ["标准号：GB 1-2026\n原文：测试规范内容"])
        aliases = {item.alias_name: item.collection_name for item in self.client.get_aliases().aliases}
        self.assertEqual(aliases["corpus_current"], "corpus_abc123")
        current = json.loads(
            (self.root / "data" / "registry" / "corpus-current.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(current["corpus_version"], "corpus-abc123")

    def test_tampered_chunks_cannot_be_published(self):
        self.write_candidate()
        chunks = self.root / "data/retrieval/corpus-abc123-chunks.jsonl"
        chunks.write_text(chunks.read_text(encoding="utf-8") + " ", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            publish_corpus_candidate(
                self.root,
                client=self.client,
                embedder=FakeEmbedder(),
                enable_bm25=False,
            )

    def test_unpublished_incomplete_collection_is_rebuilt(self):
        self.write_candidate()
        self.client.create_collection(
            collection_name="corpus_abc123",
            vectors_config={
                "dense": models.VectorParams(size=3, distance=models.Distance.COSINE)
            },
        )

        result = publish_corpus_candidate(
            self.root,
            client=self.client,
            embedder=FakeEmbedder(),
            enable_bm25=False,
        )

        self.assertEqual(result["action"], "rebuilt")
        self.assertEqual(self.client.count("corpus_abc123", exact=True).count, 1)

    def test_published_collection_remains_immutable_if_it_is_damaged(self):
        self.write_candidate()
        publish_corpus_candidate(
            self.root,
            client=self.client,
            embedder=FakeEmbedder(),
            enable_bm25=False,
        )
        self.client.delete_collection("corpus_abc123")
        self.client.create_collection(
            collection_name="corpus_abc123",
            vectors_config={
                "dense": models.VectorParams(size=3, distance=models.Distance.COSINE)
            },
        )

        with self.assertRaisesRegex(RuntimeError, "immutable collection"):
            publish_corpus_candidate(
                self.root,
                client=self.client,
                embedder=FakeEmbedder(),
                enable_bm25=False,
            )

    def test_candidate_with_failed_pages_cannot_replace_current_corpus(self):
        self.write_candidate(status="needs_attention")

        with self.assertRaisesRegex(ValueError, "not ready"):
            publish_corpus_candidate(
                self.root, client=self.client, embedder=FakeEmbedder()
            )

        self.assertFalse(
            (self.root / "data" / "registry" / "corpus-current.json").exists()
        )
        self.assertNotIn(
            "corpus_current",
            {item.alias_name for item in self.client.get_aliases().aliases},
        )


if __name__ == "__main__":
    unittest.main()
