"""Public-deployment regressions: live alias and page-only document access."""

from __future__ import annotations

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


ROOT = Path(__file__).resolve().parents[1]


def _query_service() -> QueryApplicationService:
    return QueryApplicationService(
        scope_resolver=PassthroughScopeResolver(),
        evidence_retriever=InMemoryEvidenceRetriever([]),
        answer_generator=InMemoryAnswerGenerator(None),
    )


class PublicDeploymentContractTests(unittest.TestCase):
    def test_compose_and_image_default_to_the_live_query_alias(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("${QDRANT_COLLECTION:-corpus_v2_live}", compose)
        self.assertIn("QDRANT_COLLECTION=corpus_v2_live", dockerfile)

    def test_document_api_exposes_pages_but_never_the_complete_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "data" / "raw" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            with pymupdf.open() as pdf:
                pdf.new_page(width=200, height=300).insert_text((30, 50), "page one")
                pdf.new_page(width=200, height=300).insert_text((30, 50), "page two")
                pdf.save(pdf_path)
            sha = file_digest(pdf_path)
            inventory = root / "data" / "registry" / "inventory.csv"
            inventory.parent.mkdir(parents=True)
            inventory.write_text(
                f"sha256,file_name,rel_path\n{sha},example.pdf,data/raw/example.pdf\n",
                encoding="utf-8",
            )
            catalog = PublicDocumentCatalog(project_root=root, inventory_path=inventory)
            client = TestClient(
                create_app(
                    query_service=_query_service(),
                    document_catalog=catalog,
                    document_page_service=DocumentPageImageService(catalog),
                )
            )

            metadata = client.get(f"/api/v1/documents/{sha}")
            page = client.get(f"/api/v1/documents/{sha}/pages/1/image")

            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["page_count"], 2)
            self.assertEqual(
                metadata.json()["page_image_url_template"],
                f"/api/v1/documents/{sha}/pages/{{physical_page}}/image",
            )
            self.assertNotIn("local_content_url", metadata.json())
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["content-type"], "image/png")
            self.assertTrue(page.content.startswith(b"\x89PNG"))
            self.assertEqual(
                client.get(f"/api/v1/documents/{sha}/pages/3/image").status_code,
                404,
            )
            self.assertEqual(
                client.get(f"/api/v1/documents/{sha}/content").status_code,
                404,
            )

    def test_frontend_has_no_complete_pdf_link(self) -> None:
        html = (ROOT / "src" / "server" / "static" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("/content#page=", html)
        self.assertIn("/pages/${page}/image", html)
        self.assertIn("/api/v1/runtime", html)

    def test_runtime_profile_is_public_but_diagnostics_are_not(self) -> None:
        client = TestClient(
            create_app(query_service=_query_service(), deployment_profile="demo")
        )

        self.assertEqual(
            client.get("/api/v1/runtime").json(),
            {
                "deployment_profile": "demo",
                "answer_access": "shared_demo_budget",
                "answer_available": True,
            },
        )
        self.assertEqual(client.get("/api/v1/formulas").status_code, 404)
        self.assertEqual(client.get("/api/v1/formula-review").status_code, 404)
        paths = client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/api/v1/formulas", paths)
        self.assertNotIn("/api/v1/formula-review", paths)



if __name__ == "__main__":
    unittest.main()
