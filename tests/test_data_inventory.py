from __future__ import annotations

import hashlib
import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.data_inventory import (
    InventoryRow,
    detect_relationships,
    extract_std_no,
    file_sha256,
)


def make_row(
    file_name: str,
    *,
    sha256: str,
    std_no: str = "",
    text_coverage: float = 1.0,
) -> InventoryRow:
    return InventoryRow(
        file_name=file_name,
        rel_path=f"data/raw/{file_name}",
        sha256=sha256,
        size_mb=1.0,
        pages=1,
        text_coverage=text_coverage,
        extraction_route_candidate="native",
        std_no=std_no,
        metadata_source="filename" if std_no else "",
        document_kind="standard" if std_no else "unknown",
        jurisdiction="全国",
        official_source_uri="",
        source_review="needs_review",
        effective_status="unknown",
        same_standard_as="",
        exact_duplicate_of="",
        inventory_status="candidate",
        note="",
    )


class InventoryRelationshipTests(unittest.TestCase):
    def test_extracts_municipal_local_standard_number(self):
        self.assertEqual(
            extract_std_no("DB 4201/T 651-2021 武汉市排水管道混错接改造技术规程.pdf"),
            ("DB4201/T 651-2021", "local"),
        )

    def test_file_sha256_matches_known_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            asset = Path(folder) / "sample.pdf"
            asset.write_bytes(b"same bytes")

            self.assertEqual(
                file_sha256(asset), hashlib.sha256(b"same bytes").hexdigest()
            )

    def test_identical_bytes_are_exact_duplicates(self):
        primary = make_row("named.pdf", sha256="a" * 64, std_no="GB 1-2024")
        renamed = make_row("renamed.pdf", sha256="a" * 64)

        detect_relationships([primary, renamed])

        self.assertEqual(renamed.exact_duplicate_of, primary.file_name)
        self.assertEqual(renamed.inventory_status, "exact_duplicate")

    def test_same_standard_number_is_not_treated_as_an_exact_duplicate(self):
        searchable = make_row(
            "searchable.pdf",
            sha256="a" * 64,
            std_no="T/CECS 758-2020",
            text_coverage=1.0,
        )
        scanned = make_row(
            "scanned.pdf",
            sha256="b" * 64,
            std_no="T/CECS 758-2020",
            text_coverage=0.0,
        )

        detect_relationships([searchable, scanned])

        self.assertEqual(scanned.same_standard_as, searchable.file_name)
        self.assertEqual(scanned.exact_duplicate_of, "")
        self.assertEqual(scanned.inventory_status, "candidate")


class ApprovedSampleManifestTests(unittest.TestCase):
    def test_manifest_references_current_non_duplicate_inventory_assets(self):
        registry_dir = Path("data/registry")
        with (registry_dir / "inventory.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            inventory = {row["file_name"]: row for row in csv.DictReader(stream)}
        with (registry_dir / "document_reviews.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            reviews = {row["file_name"]: row for row in csv.DictReader(stream)}
        manifest = json.loads(
            (registry_dir / "experiment-sample-v0.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(manifest["status"], "approved_for_experiment")
        self.assertEqual(len(manifest["documents"]), 8)
        self.assertEqual(len(reviews), 8)

        for document in manifest["documents"]:
            name = document["file_name"]
            self.assertIn(name, inventory)
            self.assertIn(name, reviews)
            self.assertEqual(document["sha256"], inventory[name]["sha256"])
            self.assertEqual(inventory[name]["exact_duplicate_of"], "")
            self.assertIn(
                reviews[name]["selection_status"],
                {"approved_experiment", "approved_formal"},
            )


if __name__ == "__main__":
    unittest.main()
