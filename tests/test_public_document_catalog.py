"""Public document browsing is inventory-backed and separate from answer admission."""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf

from src.adapters.public_document_catalog import PublicDocumentCatalog


class PublicDocumentCatalogTests(unittest.TestCase):
    def test_inventory_exposes_every_file_alias_grouped_by_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            first = raw / "first.pdf"
            duplicate = raw / "duplicate.pdf"
            second = raw / "second.pdf"
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), "first")
                pdf.save(first)
            duplicate.write_bytes(first.read_bytes())
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), "second")
                pdf.save(second)
            first_sha = hashlib.sha256(first.read_bytes()).hexdigest()
            second_sha = hashlib.sha256(second.read_bytes()).hexdigest()
            inventory = root / "data" / "registry" / "inventory.csv"
            inventory.parent.mkdir(parents=True)
            with inventory.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "sha256", "rel_path", "file_name", "std_no",
                        "document_kind", "jurisdiction", "official_source_uri",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "sha256": first_sha,
                            "rel_path": "data/raw/first.pdf",
                            "file_name": "first.pdf",
                        },
                        {
                            "sha256": first_sha,
                            "rel_path": "data/raw/duplicate.pdf",
                            "file_name": "duplicate.pdf",
                        },
                        {
                            "sha256": second_sha,
                            "rel_path": "data/raw/second.pdf",
                            "file_name": "second.pdf",
                        },
                    ]
                )

            catalog = PublicDocumentCatalog(project_root=root, inventory_path=inventory)
            documents = catalog.list()

            self.assertEqual(len(documents), 2)
            self.assertEqual(sum(len(item.aliases) for item in documents), 3)
            first_document = catalog.get(first_sha)
            self.assertIsNotNone(first_document)
            self.assertEqual(
                set(first_document.aliases),
                {"first.pdf", "duplicate.pdf"},
            )
            self.assertEqual(first_document.local_path, first)

    def test_hash_mismatch_never_exposes_local_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "data" / "raw" / "changed.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"changed")
            inventory = root / "data" / "registry" / "inventory.csv"
            inventory.parent.mkdir(parents=True)
            inventory.write_text(
                "sha256,rel_path,file_name\n"
                + f"{'a' * 64},data/raw/changed.pdf,changed.pdf\n",
                encoding="utf-8",
            )

            document = PublicDocumentCatalog(
                project_root=root, inventory_path=inventory
            ).get("a" * 64)

            self.assertIsNotNone(document)
            self.assertIsNone(document.local_path)


if __name__ == "__main__":
    unittest.main()
