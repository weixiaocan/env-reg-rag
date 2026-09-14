import csv
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.adapters.inventory_document_catalog import InventoryDocumentCatalog
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.server.fastapi_app import create_app


class DocumentHttpApiTest(unittest.TestCase):
    def test_exact_duplicate_files_share_one_document_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            (raw / "canonical.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
            (raw / "alias.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
            inventory_path = root / "inventory.csv"
            with inventory_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "file_name",
                        "rel_path",
                        "sha256",
                        "std_no",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "file_name": "canonical.pdf",
                        "rel_path": "data/raw/canonical.pdf",
                        "sha256": "b" * 64,
                        "std_no": "GB TEST-2026",
                    }
                )
                writer.writerow(
                    {
                        "file_name": "alias.pdf",
                        "rel_path": "data/raw/alias.pdf",
                        "sha256": "b" * 64,
                        "std_no": "",
                    }
                )

            catalog = InventoryDocumentCatalog(
                project_root=root,
                inventory_path=inventory_path,
            )

            document = catalog.get(f"doc_{'b' * 16}")
            self.assertIsNotNone(document)
            self.assertEqual(document.file_name, "canonical.pdf")
            self.assertEqual(document.standard_no, "GB TEST-2026")

    def test_user_can_open_an_allowlisted_pdf_at_its_cited_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "data" / "raw" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            inventory_path = root / "data" / "registry" / "inventory.csv"
            inventory_path.parent.mkdir(parents=True)
            with inventory_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "file_name",
                        "rel_path",
                        "sha256",
                        "official_source_uri",
                        "std_no",
                        "document_kind",
                        "jurisdiction",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "file_name": "example.pdf",
                        "rel_path": "data/raw/example.pdf",
                        "sha256": "a" * 64,
                        "official_source_uri": "https://example.org/official.pdf",
                        "std_no": "T/TEST 1-2026",
                        "document_kind": "standard",
                        "jurisdiction": "全国",
                    }
                )
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=InventoryDocumentCatalog(
                        project_root=root,
                        inventory_path=inventory_path,
                    ),
                )
            )
            document_id = f"doc_{'a' * 16}"

            metadata = client.get(f"/api/v1/documents/{document_id}")
            content = client.get(f"/api/v1/documents/{document_id}/content")

            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(
                metadata.json(),
                {
                    "document_version_id": document_id,
                    "file_name": "example.pdf",
                    "standard_no": "T/TEST 1-2026",
                    "document_kind": "standard",
                    "jurisdiction": "全国",
                    "source_uri": "https://example.org/official.pdf",
                    "local_content_available": True,
                    "local_content_url": f"/api/v1/documents/{document_id}/content",
                },
            )
            self.assertEqual(content.status_code, 200)
            self.assertEqual(content.headers["content-type"], "application/pdf")
            self.assertEqual(content.content, b"%PDF-1.4\n%%EOF\n")

    def test_unknown_document_has_a_stable_not_found_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "inventory.csv"
            inventory_path.write_text("file_name,rel_path,sha256\n", encoding="utf-8")
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=InventoryDocumentCatalog(
                        project_root=root,
                        inventory_path=inventory_path,
                    ),
                )
            )

            response = client.get("/api/v1/documents/doc_missing")

            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                response.json(),
                {
                    "error": {
                        "code": "document_not_found",
                        "message": "document is not registered",
                    }
                },
            )

    @staticmethod
    def _query_service() -> QueryApplicationService:
        return QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )


if __name__ == "__main__":
    unittest.main()
