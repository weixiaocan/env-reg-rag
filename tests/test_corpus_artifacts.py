from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.application.corpus_artifacts import resolve_current_corpus


class CurrentCorpusArtifactsTest(unittest.TestCase):
    def test_uses_published_pointer_when_present(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "data" / "registry"
            registry.mkdir(parents=True)
            manifest_path = registry / "corpus-abc.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "corpus_version": "corpus-abc",
                        "status": "ready",
                        "evidence_units": "data/evidence/corpus-abc-evidence.jsonl",
                        "retrieval_chunks": "data/retrieval/corpus-abc-chunks.jsonl",
                    }
                ),
                encoding="utf-8",
            )
            (registry / "corpus-current.json").write_text(
                json.dumps(
                    {
                        "status": "published",
                        "corpus_version": "corpus-abc",
                        "manifest": "data/registry/corpus-abc.json",
                        "collection_name": "corpus_abc",
                        "query_alias": "corpus_current",
                    }
                ),
                encoding="utf-8",
            )

            current = resolve_current_corpus(root)

            self.assertEqual(current.corpus_version, "corpus-abc")
            self.assertEqual(current.collection_name, "corpus_abc")
            self.assertEqual(current.manifest_path, manifest_path)
            self.assertEqual(
                current.evidence_units_path,
                root / "data/evidence/corpus-abc-evidence.jsonl",
            )

    def test_falls_back_to_existing_formal_corpus_before_first_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            current = resolve_current_corpus(root)

            self.assertEqual(current.corpus_version, "formal-corpus-v1")
            self.assertEqual(current.collection_name, "corpus_formal_v1")

    def test_rejects_pointer_manifest_version_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "data" / "registry"
            registry.mkdir(parents=True)
            (registry / "manifest.json").write_text(
                json.dumps({"corpus_version": "corpus-other", "status": "ready"}),
                encoding="utf-8",
            )
            (registry / "corpus-current.json").write_text(
                json.dumps(
                    {
                        "status": "published",
                        "corpus_version": "corpus-abc",
                        "manifest": "data/registry/manifest.json",
                        "collection_name": "corpus_abc",
                        "query_alias": "corpus_current",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                resolve_current_corpus(root)


if __name__ == "__main__":
    unittest.main()
