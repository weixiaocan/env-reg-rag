"""End-to-end HTTP seam for the public page-by-page document browser."""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from src.adapters.pdf_region_renderer import DocumentPageImageService
from src.adapters.public_document_catalog import PublicDocumentCatalog
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.server.fastapi_app import create_app


class DocumentBrowserHttpTests(unittest.TestCase):
    def test_browser_lists_documents_and_loads_only_page_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "data" / "raw" / "example.pdf"
            pdf_path.parent.mkdir(parents=True)
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), "page one")
                pdf.new_page().insert_text((40, 60), "page two")
                pdf.save(pdf_path)
            sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            inventory = root / "data" / "registry" / "inventory.csv"
            inventory.parent.mkdir(parents=True)
            with inventory.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["sha256", "rel_path", "file_name"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "sha256": sha,
                        "rel_path": "data/raw/example.pdf",
                        "file_name": "example.pdf",
                    }
                )
            catalog = PublicDocumentCatalog(
                project_root=root, inventory_path=inventory
            )
            service = QueryApplicationService(
                scope_resolver=RuleBasedScopeResolver(),
                evidence_retriever=InMemoryEvidenceRetriever([]),
                answer_generator=InMemoryAnswerGenerator(None),
            )
            client = TestClient(
                create_app(
                    query_service=service,
                    document_catalog=catalog,
                    document_page_service=DocumentPageImageService(catalog),
                )
            )

            browser = client.get("/documents")
            listing = client.get("/api/v1/documents")
            page = client.get(f"/api/v1/documents/{sha}/pages/2/image")

            self.assertEqual(browser.status_code, 200)
            self.assertIn("资料逐页浏览", browser.text)
            self.assertNotIn("/content", browser.text)
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(len(listing.json()), 1)
            self.assertEqual(listing.json()[0]["page_count"], 2)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["content-type"], "image/png")


if __name__ == "__main__":
    unittest.main()
