from __future__ import annotations

import unittest

from src.application.pdf_source_audit import (
    _document_observations,
    _scan_effective_status,
    resolve_source_fields,
    source_metadata,
)
from src.domain.source_evidence import SourceObservation


def _file_ref(digest: str = "a" * 64, page: int = 1) -> dict:
    return {"kind": "file", "sha256": digest, "physical_pages": [page], "excerpt": "GB 50318-2017"}


def _doc_obs(field: str, value: str, digest: str = "a" * 64, page: int = 1) -> SourceObservation:
    return SourceObservation(
        field=field, value=value, origin_kind="document_extracted", status="unverified",
        evidence_ref=_file_ref(digest, page),
    )


def _legacy_obs(field: str, value: str) -> SourceObservation:
    return SourceObservation(
        field=field, value=value, origin_kind="legacy_record", status="unverified",
        evidence_ref={"kind": "registry", "path": "data/registry/inventory.csv", "row": 2,
                      "record_sha256": "b" * 64},
    )


class StandardNumberConflictTest(unittest.TestCase):
    def test_conflict_records_both_values_and_suggests_pdf_extracted(self):
        items = [
            _legacy_obs("standard_number", "T/CMSA-2019"),
            _doc_obs("standard_number", "T/CMSA 0013-2019"),
        ]
        resolved = resolve_source_fields(items)["standard_number"]
        self.assertEqual(resolved["status"], "conflicting")
        self.assertIsNone(resolved["value"])
        self.assertEqual(resolved["conflict_values"], ["T/CMSA 0013-2019", "T/CMSA-2019"])
        # The PDF-body (document_extracted) value is the suggested adoption.
        self.assertEqual(resolved["suggested_value"], "T/CMSA 0013-2019")

    def test_source_metadata_adopts_suggested_standard_number(self):
        record = {
            "schema_version": "1", "file_sha256": "a" * 64, "aliases": [],
            "observations": [
                _legacy_obs("standard_number", "T/CMSA-2019").to_dict(),
                _doc_obs("standard_number", "T/CMSA 0013-2019").to_dict(),
            ],
        }
        metadata = source_metadata(record)
        self.assertEqual(metadata["standard_number"], "T/CMSA 0013-2019")
        self.assertEqual(metadata["standard_number_conflict"], "true")
        self.assertIn("T/CMSA-2019", metadata["standard_number_alternatives"])


class EffectiveStatusExtractionTest(unittest.TestCase):
    def test_self_repealed_marker_marks_repealed(self):
        text = "本规范自2025年起废止，请使用新版。"
        findings = _scan_effective_status(text, set())
        values = [v for _, v in findings]
        self.assertIn("repealed", values)

    def test_other_standard_superseded_is_not_self_repealed(self):
        # "GB 50318-2000 同时废止" on a GB 50318-2017 document means it
        # supersedes the 2000 revision — the document itself is current.
        text = "GB 50318-2000 同时废止。"
        findings = _scan_effective_status(text, {"GB 50318-2017"})
        values = [v for _, v in findings]
        self.assertNotIn("repealed", values)
        self.assertIn("superseded", values)

    def test_no_known_std_no_does_not_guess_superseded(self):
        text = "GB 50318-2000 同时废止。"
        findings = _scan_effective_status(text, set())
        values = [v for _, v in findings]
        self.assertNotIn("superseded", values)
        self.assertNotIn("repealed", values)

    def test_draft_marker_marks_draft(self):
        text = "本标准征求意见稿。"
        findings = _scan_effective_status(text, set())
        self.assertIn("draft", [v for _, v in findings])

    def test_document_observations_emits_effective_status(self):
        digest = "c" * 64
        text = "本规范废止。"
        obs = _document_observations(text, digest, page=2, known_std_nos=set())
        statuses = [o for o in obs if o["field"] == "effective_status"]
        self.assertTrue(statuses)
        self.assertEqual(statuses[0]["value"], "repealed")
        self.assertEqual(statuses[0]["origin_kind"], "document_extracted")
        self.assertEqual(statuses[0]["evidence_ref"]["physical_pages"], [2])

    def test_source_metadata_passes_through_pdf_effective_status(self):
        record = {
            "schema_version": "1", "file_sha256": "a" * 64, "aliases": [],
            "observations": [
                _doc_obs("effective_status", "repealed").to_dict(),
            ],
        }
        metadata = source_metadata(record)
        self.assertEqual(metadata["effective_status"], "repealed")

    def test_source_metadata_keeps_unknown_without_pdf_marker(self):
        record = {
            "schema_version": "1", "file_sha256": "a" * 64, "aliases": [],
            "observations": [
                _legacy_obs("effective_status", "current").to_dict(),
            ],
        }
        metadata = source_metadata(record)
        # Legacy-only effective_status is not trusted; no PDF marker → unknown.
        self.assertEqual(metadata["effective_status"], "unknown")
        self.assertEqual(metadata["legacy_effective_status"], "current")


if __name__ == "__main__":
    unittest.main()
