import tempfile
import unittest
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient
from src.adapters.pdf_region_renderer import DocumentPageImageService

from src.adapters.public_document_catalog import PublicDocumentCatalog
from src.application.pdf_source_audit import file_digest
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import PassthroughScopeResolver
from src.server.fastapi_app import create_app


def _write_inventory(root: Path, rel_path: str) -> tuple[Path, str]:
    """Register one PDF in inventory.csv the way the live catalog reads it."""
    sha = file_digest(root / rel_path)
    inventory = root / "data" / "registry" / "inventory.csv"
    inventory.parent.mkdir(parents=True, exist_ok=True)
    inventory.write_text(
        f"sha256,file_name,rel_path\n{sha},{Path(rel_path).name},{rel_path}\n",
        encoding="utf-8",
    )
    return inventory, sha


class DocumentHttpApiTest(unittest.TestCase):
    def test_user_can_open_an_allowlisted_pdf_page_without_pdf_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "data" / "raw" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            with pymupdf.open() as pdf:
                pdf.new_page(width=200, height=300).insert_text((30, 50), "page one")
                pdf.save(pdf_path)
            inventory, sha = _write_inventory(root, "data/raw/example.pdf")
            catalog = PublicDocumentCatalog(project_root=root, inventory_path=inventory)
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=catalog,
                    document_page_service=DocumentPageImageService(catalog),
                )
            )

            metadata = client.get(f"/api/v1/documents/{sha}")
            page = client.get(f"/api/v1/documents/{sha}/pages/1/image")

            self.assertEqual(metadata.status_code, 200)
            body = metadata.json()
            self.assertEqual(body["document_version_id"], sha)
            self.assertEqual(body["file_name"], "example.pdf")
            self.assertTrue(body["local_page_images_available"])
            self.assertEqual(body["page_count"], 1)
            self.assertEqual(
                body["page_image_url_template"],
                f"/api/v1/documents/{sha}/pages/{{physical_page}}/image",
            )
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["content-type"], "image/png")
            geometry = client.get(f"/api/v1/documents/{sha}/pages/1")
            self.assertEqual(
                geometry.json(),
                {"physical_page": 1, "page_width_pt": 200.0, "page_height_pt": 300.0},
            )
            self.assertEqual(
                client.get(f"/api/v1/documents/{sha}/pages/2").status_code,
                404,
            )
            self.assertEqual(
                client.get(f"/api/v1/documents/{sha}/content").status_code,
                404,
            )

    def test_unknown_document_has_a_stable_not_found_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "inventory.csv"
            inventory.write_text("sha256,file_name,rel_path\n", encoding="utf-8")
            client = TestClient(
                create_app(
                    query_service=self._query_service(),
                    document_catalog=PublicDocumentCatalog(
                        project_root=root, inventory_path=inventory
                    ),
                )
            )
            response = client.get("/api/v1/documents/unknown_sha")
            self.assertEqual(response.status_code, 404)

    @staticmethod
    def _query_service() -> QueryApplicationService:
        return QueryApplicationService(
            scope_resolver=PassthroughScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )


if __name__ == "__main__":
    unittest.main()
