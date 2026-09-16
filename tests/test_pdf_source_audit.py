from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import pymupdf

from src.application.pdf_source_audit import (
    audit_sources, completeness_metadata, load_source_records, resolve_source_fields, write_audit,
)
from src.domain.source_evidence import SourceObservation


def observation(field="publication_date", value="2026-01-01", **overrides):
    item = dict(field=field, value=value, origin_kind="external_verified",
                status="verified", evidence_ref={"kind": "url", "url": "https://example.org/notice"},
                checked_at="2026-09-16T00:00:00+00:00", verification_method="manual_field_comparison")
    item.update(overrides)
    return SourceObservation(**item)


class SourceObservationTest(unittest.TestCase):
    def test_unknown_placeholder_is_preserved_without_conflicting_with_a_concrete_claim(self):
        items = [observation("document_kind", value, status="unverified",
                             origin_kind="provider_declared", checked_at="", verification_method="")
                 for value in ("standard", "unknown")]
        resolved = resolve_source_fields(items)["document_kind"]
        self.assertEqual(resolved["value"], "standard")
        self.assertEqual(resolved["status"], "unverified")
        self.assertFalse(resolved["has_disagreement"])
        self.assertEqual(len(resolved["observations"]), 2)

    def test_verification_requires_evidence_and_an_actual_check_time(self):
        for overrides in ({"evidence_ref": {}}, {"checked_at": ""},
                          {"verification_method": ""}, {"checked_at": "not-a-date"},
                          {"origin_kind": "provider_declared"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                observation(**overrides).validate()

    def test_legacy_claim_cannot_be_promoted_to_verified(self):
        with self.assertRaises(ValueError):
            observation(origin_kind="legacy_record").validate()

    def test_conflicting_verified_values_are_not_silently_ranked(self):
        resolved = resolve_source_fields([observation(), observation(value="2025-01-01")])
        self.assertEqual(resolved["publication_date"]["status"], "conflicting")
        self.assertIsNone(resolved["publication_date"]["value"])

    def test_unverified_differences_remain_visible_but_do_not_override_verified(self):
        resolved = resolve_source_fields([
            observation(), observation(value="2025-01-01", status="unverified",
                                       origin_kind="provider_declared", checked_at="", verification_method=""),
        ])
        self.assertEqual(resolved["publication_date"]["value"], "2026-01-01")
        self.assertTrue(resolved["publication_date"]["has_disagreement"])

    def test_absolute_paths_and_authenticated_urls_are_rejected(self):
        for ref in ({"kind": "registry", "path": "/private/reviews.csv", "row": 2, "record_sha256": "a" * 64},
                    {"kind": "file", "sha256": "a" * 64, "physical_pages": [0]},
                    {"kind": "url", "url": "https://user:password@example.org/pdf"}):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                observation(evidence_ref=ref).validate()


class PdfSourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data/raw").mkdir(parents=True)
        (self.root / "data/registry").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def pdf(self, name="standard.pdf", text="Standard text", pages=2):
        path = self.root / "data/raw" / name
        with pymupdf.open() as doc:
            for _ in range(pages):
                doc.new_page().insert_text((72, 72), text)
            doc.save(path)
        return path

    def inventory(self, paths):
        rows = [{"file_name": path.name, "rel_path": path.relative_to(self.root).as_posix(),
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": "2",
                 "std_no": "GB 1-2026", "document_kind": "standard", "jurisdiction": "全国"}
                for path in paths]
        with (self.root / "data/registry/inventory.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return rows

    def test_dry_run_preserves_old_claims_without_inventing_verification(self):
        paths = [self.pdf()]
        self.inventory(paths)
        (self.root / "data/registry/document_reviews.csv").write_text(
            "file_name,official_source_uri,source_review,publication_date\n"
            "standard.pdf,https://example.org/pdf,official_fulltext_verified,2026-01-01\n",
            encoding="utf-8",
        )
        result = audit_sources(self.root)
        self.assertFalse((self.root / "data/registry/source_evidence.jsonl").exists())
        legacy = [item for item in result["records"][0]["observations"]
                  if item["field"] == "source_review"]
        self.assertEqual(legacy[0]["value"], "official_fulltext_verified")
        self.assertEqual(legacy[0]["status"], "unverified")
        self.assertEqual(legacy[0]["checked_at"], "")
        self.assertEqual(legacy[0]["evidence_ref"]["row"], 2)
        self.assertEqual(result["files"][0]["completeness"]["status"], "unverified")

    def test_exact_copies_have_one_content_record_and_all_file_results(self):
        path = self.pdf()
        copy = self.root / "data/raw/copy.pdf"
        copy.write_bytes(path.read_bytes())
        self.inventory([path, copy])
        result = audit_sources(self.root)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(result["records"][0]["aliases"]), 2)
        self.assertEqual(len(result["files"]), 2)

    def test_registry_survives_rename_and_migration_is_idempotent(self):
        path = self.pdf()
        self.inventory([path])
        first = audit_sources(self.root)
        first["records"][0]["observations"].append(observation().to_dict())
        write_audit(self.root, first)
        renamed = path.with_name("renamed.pdf")
        path.rename(renamed)
        self.inventory([renamed])
        second = audit_sources(self.root)
        write_audit(self.root, second)
        third = audit_sources(self.root)
        self.assertEqual(second["records"], third["records"])
        self.assertEqual(second["files"][0]["fields"]["publication_date"]["status"], "verified")
        self.assertEqual(len(load_source_records(self.root)), 1)

    def test_changed_bytes_do_not_inherit_old_approval(self):
        path = self.pdf()
        self.inventory([path])
        result = audit_sources(self.root)
        result["records"][0]["observations"].append(observation().to_dict())
        write_audit(self.root, result)
        path.unlink()
        self.inventory([self.pdf(text="Changed text")])
        changed = audit_sources(self.root)
        self.assertNotIn("publication_date", changed["files"][0]["fields"])
        self.assertEqual(changed["summary"]["orphan_record_count"], 1)

    def test_expected_page_count_mismatch_is_a_suspicion_not_a_parse_block(self):
        path = self.pdf()
        self.inventory([path])
        result = audit_sources(self.root)
        result["records"][0]["observations"].append(observation("expected_page_count", "3").to_dict())
        write_audit(self.root, result)
        audited = audit_sources(self.root)
        check = audited["files"][0]["completeness"]
        self.assertEqual(check["status"], "suspected")
        self.assertIn("expected_page_count_mismatch", check["issues"])
        self.assertEqual(check["readable_page_count"], 2)

    def test_matching_page_count_alone_does_not_prove_completeness(self):
        path = self.pdf()
        self.inventory([path])
        result = audit_sources(self.root)
        result["records"][0]["observations"].append(observation("expected_page_count", "2").to_dict())
        write_audit(self.root, result)
        self.assertEqual(audit_sources(self.root)["files"][0]["completeness"]["status"], "unverified")

    def test_broken_pdf_is_reported_and_does_not_abort_other_files(self):
        broken = self.root / "data/raw/broken.pdf"
        broken.write_bytes(b"broken pdf")
        self.inventory([broken, self.pdf()])
        result = audit_sources(self.root)
        bad = next(item for item in result["files"] if item["rel_path"].endswith("broken.pdf"))
        self.assertIn("unreadable_pdf", bad["completeness"]["issues"])
        self.assertEqual(len(result["files"]), 2)

    def test_inventory_path_cannot_escape_raw_directory(self):
        rows = self.inventory([self.pdf()])
        rows[0]["rel_path"] = "../outside.pdf"
        with (self.root / "data/registry/inventory.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        with self.assertRaises(ValueError):
            audit_sources(self.root)

    def test_native_cover_claim_is_hash_and_page_bound_but_not_officially_verified(self):
        self.inventory([self.pdf(text="GB/T 123-2026")])
        result = audit_sources(self.root)
        claims = result["files"][0]["fields"]["standard_number"]["observations"]
        natives = [c for c in claims if c["origin_kind"] == "document_extracted"]
        native = natives[0]
        self.assertEqual(native["value"], "GB/T 123-2026")
        self.assertEqual(native["status"], "unverified")
        self.assertEqual({tuple(c["evidence_ref"]["physical_pages"]) for c in natives}, {(1,), (2,)})
        self.assertEqual(native["evidence_ref"]["sha256"], result["files"][0]["file_sha256"])

    def test_supported_completeness_requires_full_comparison_not_just_a_url(self):
        self.inventory([self.pdf()])
        with self.assertRaises(ValueError):
            observation("completeness", "complete").validate()
        result = audit_sources(self.root)
        result["records"][0]["observations"].append(
            observation("completeness", "complete", verification_method="full_document_comparison").to_dict()
        )
        write_audit(self.root, result)
        checked = audit_sources(self.root)
        self.assertEqual(checked["files"][0]["completeness"]["status"], "evidence_supported")
        self.assertTrue(checked["files"][0]["completeness"]["supported_by"])

    def test_printed_page_gap_is_a_hint_not_a_missing_page_certification(self):
        path = self.pdf()
        with pymupdf.open(path) as doc:
            doc[0].insert_text((280, 805), "2")
            doc[1].insert_text((280, 805), "4")
            doc.saveIncr()
        self.inventory([path])
        result = audit_sources(self.root)
        check = result["files"][0]["completeness"]
        self.assertIn("printed_page_sequence_discontinuity", check["issues"])
        self.assertEqual(check["issue_pages"]["printed_page_sequence_discontinuity"], [2])

    def test_native_readability_does_not_claim_scanned_text_was_checked(self):
        self.inventory([self.pdf(text="")])
        check = audit_sources(self.root)["files"][0]["completeness"]
        self.assertEqual(check["readable_page_count"], 2)
        self.assertEqual(check["native_text_page_count"], 0)
        self.assertEqual(check["status"], "unverified")

    def test_changed_registry_invalidates_old_completeness_report(self):
        self.inventory([self.pdf()])
        result = audit_sources(self.root)
        write_audit(self.root, result)
        self.assertEqual(len(completeness_metadata(self.root)), 1)
        registry = self.root / "data/registry/source_evidence.jsonl"
        registry.write_text(registry.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(completeness_metadata(self.root), {})

    def test_ambiguous_legacy_filename_is_not_bound_to_two_different_contents(self):
        first = self.pdf()
        second = self.pdf(name="other.pdf", text="different")
        rows = self.inventory([first, second])
        rows[1]["file_name"] = rows[0]["file_name"]
        with (self.root / "data/registry/inventory.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (self.root / "data/registry/document_reviews.csv").write_text(
            "file_name,source_review\nstandard.pdf,official_fulltext_verified\n", encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            audit_sources(self.root)

    def test_audit_does_not_require_network_access(self):
        self.inventory([self.pdf()])
        with mock.patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(audit_sources(self.root)["summary"]["file_count"], 1)

    def test_changed_inventory_invalidates_completeness_report(self):
        self.inventory([self.pdf()])
        write_audit(self.root, audit_sources(self.root))
        path = self.root / "data/registry/inventory.csv"
        text = path.read_text(encoding="utf-8").replace(",2,GB 1-2026,", ",3,GB 1-2026,")
        path.write_text(text, encoding="utf-8")
        self.assertEqual(completeness_metadata(self.root), {})

    def test_inventory_line_endings_do_not_invalidate_the_same_content(self):
        self.inventory([self.pdf()])
        write_audit(self.root, audit_sources(self.root))
        path = self.root / "data/registry/inventory.csv"
        text = path.read_text(encoding="utf-8")
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        self.assertEqual(len(completeness_metadata(self.root)), 1)

    def test_extraction_cannot_be_attached_to_another_files_hash(self):
        self.inventory([self.pdf()])
        result = audit_sources(self.root)
        result["records"][0]["observations"].append(observation(
            origin_kind="document_extracted",
            evidence_ref={"kind": "file", "sha256": "a" * 64, "physical_pages": [1]},
        ).to_dict())
        with self.assertRaises(ValueError):
            write_audit(self.root, result)

    def test_custom_inventory_cannot_reuse_diagnostics_for_different_rows(self):
        rows = self.inventory([self.pdf()])
        write_audit(self.root, audit_sources(self.root))
        rows[0]["pages"] = "3"
        self.assertEqual(completeness_metadata(self.root, inventory_rows=rows), {})

    def test_cover_dates_are_extracted_with_a_literal_excerpt(self):
        path = self.pdf()
        with pymupdf.open(path) as doc:
            doc[0].insert_text((72, 120), "2026-01-01发布 2026-02-01实施", fontname="china-s")
            doc.saveIncr()
        self.inventory([path])
        fields = audit_sources(self.root)["files"][0]["fields"]
        self.assertEqual(fields["publication_date"]["value"], "2026-01-01")
        self.assertEqual(fields["effective_from"]["value"], "2026-02-01")
        self.assertEqual(fields["publication_date"]["status"], "unverified")
        self.assertIn("发布", fields["publication_date"]["observations"][0]["evidence_ref"]["excerpt"])


if __name__ == "__main__":
    unittest.main()
