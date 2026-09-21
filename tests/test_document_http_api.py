import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.adapters.source_evidence_document_catalog import (
    SourceEvidenceDocumentCatalog,
)
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.server.fastapi_app import create_app


def _write_source_evidence(root: Path, records: list[dict]) -> Path:
    registry_path = root / "data" / "registry" / "source_evidence.jsonl"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with registry_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return registry_path


class SourceEvidenceDocumentCatalogTest(unittest.TestCase):
    def test_resolves_full_sha256_to_local_pdf_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "data" / "raw" / "example.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sha = "a" * 64
            registry_path = _write_source_evidence(
                root,
                [
                    {
                        "file_sha256": sha,
                        "aliases": [
                            {"file_name": "example.pdf", "rel_path": "data/raw/example.pdf"}
                        ],
                    }
                ],
            )
            catalog = SourceEvidenceDocumentCatalog(
                project_root=root, registry_path=registry_path
            )
            document = catalog.get(sha)
            self.assertIsNotNone(document)
            self.assertEqual(document.document_version_id, sha)
            self.assertEqual(document.file_name, "example.pdf")
            self.assertEqual(document.local_path, pdf)

    def test_rejects_path_outside_data_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha = "b" * 64
            registry_path = _write_source_evidence(
                root,
                [
                    {
                        "file_sha256": sha,
                        "aliases": [
                            {"file_name": "evil.pdf", "rel_path": "data/evil.pdf"}
                        ],
                    }
                ],
            )
            catalog = SourceEvidenceDocumentCatalog(
                project_root=root, registry_path=registry_path
            )
            document = catalog.get(sha)
            self.assertIsNotNone(document)
            self.assertIsNone(document.local_path)  # rejected (outside data/raw)

    def test_unknown_sha256_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = _write_source_evidence(root, [])
            catalog = SourceEvidenceDocumentCatalog(
                project_root=root, registry_path=registry_path
            )
            self.assertIsNone(catalog.get("c" * 64))


class DocumentHttpApiTest(unittest.TestCase):
    def test_user_can_open_an_allowlisted_pdf_at_its_cited_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "data" / "raw" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            sha = "a" * 64
            registry_path = _write_source_evidence(
                root,
                [
                    {
                        "file_sha256": sha,
                        "aliases": [
                            {"file_name": "example.pdf", "rel_path": "data/raw/example.pdf"}
                        ],
                    }
                ],
            )
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=SourceEvidenceDocumentCatalog(
                        project_root=root, registry_path=registry_path
                    ),
                )
            )

            metadata = client.get(f"/api/v1/documents/{sha}")
            content = client.get(f"/api/v1/documents/{sha}/content")

            self.assertEqual(metadata.status_code, 200)
            body = metadata.json()
            self.assertEqual(body["document_version_id"], sha)
            self.assertEqual(body["file_name"], "example.pdf")
            self.assertTrue(body["local_content_available"])
            self.assertEqual(
                body["local_content_url"], f"/api/v1/documents/{sha}/content"
            )
            self.assertEqual(content.status_code, 200)
            self.assertEqual(content.headers["content-type"], "application/pdf")
            self.assertEqual(content.content, b"%PDF-1.4\n%%EOF\n")

    def test_unknown_document_has_a_stable_not_found_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = _write_source_evidence(root, [])
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=SourceEvidenceDocumentCatalog(
                        project_root=root, registry_path=registry_path
                    ),
                )
            )
            response = client.get("/api/v1/documents/unknown_sha")
            self.assertEqual(response.status_code, 404)

    @staticmethod
    def _query_service() -> QueryApplicationService:
        return QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )


if __name__ == "__main__":
    unittest.main()
