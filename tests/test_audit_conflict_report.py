from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf

from src.application.pdf_source_audit import (
    apply_suggestions,
    audit_sources,
    collect_standard_number_conflicts,
)


def _make_pdf(path: Path, cover_text: str, pages: int = 2) -> Path:
    with pymupdf.open() as doc:
        first = doc.new_page()
        first.insert_text((72, 72), cover_text)
        for _ in range(pages - 1):
            doc.new_page().insert_text((72, 72), "body page")
        doc.save(path)
    return path


class AuditConflictReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data/raw").mkdir(parents=True)
        (self.root / "data/registry").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def _inventory(self, rows):
        with (self.root / "data/registry/inventory.csv").open("w", encoding="utf-8", newline="") as h:
            w = csv.DictWriter(h, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    def test_audit_surfaces_standard_number_conflict_with_suggestion(self):
        # Filename says T/CMSA-2019; the PDF cover prints T/CMSA 0013-2019.
        path = _make_pdf(self.root / "data/raw/cmsa.pdf", "T/CMSA 0013-2019 降雨量等级")
        rows = [{
            "file_name": path.name, "rel_path": str(path.relative_to(self.root).as_posix()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": "2",
            "std_no": "T/CMSA-2019", "document_kind": "standard", "jurisdiction": "全国",
        }]
        self._inventory(rows)
        result = audit_sources(self.root)
        conflicts = collect_standard_number_conflicts(result)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c["filename_standard_number"], "T/CMSA-2019")
        self.assertEqual(c["suggested_value"], "T/CMSA 0013-2019")
        # The per-file resolved field carries the same suggestion.
        sn = result["files"][0]["fields"]["standard_number"]
        self.assertEqual(sn["status"], "conflicting")
        self.assertEqual(sn["suggested_value"], "T/CMSA 0013-2019")

    def test_apply_suggestions_adopts_pdf_standard_number(self):
        path = _make_pdf(self.root / "data/raw/cmsa.pdf", "T/CMSA 0013-2019 降雨量等级")
        rows = [{
            "file_name": path.name, "rel_path": str(path.relative_to(self.root).as_posix()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": "2",
            "std_no": "T/CMSA-2019", "document_kind": "standard", "jurisdiction": "全国",
        }]
        self._inventory(rows)
        result = audit_sources(self.root)
        self.assertEqual(len(collect_standard_number_conflicts(result)), 1)
        applied = apply_suggestions(result)
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["adopted_standard_number"], "T/CMSA 0013-2019")
        # After applying, the conflict is resolved to verified.
        sn = result["files"][0]["fields"]["standard_number"]
        self.assertEqual(sn["status"], "verified")
        self.assertEqual(sn["value"], "T/CMSA 0013-2019")
        self.assertTrue(sn["suggestion_applied"])
        # The verified observation now exists in the record.
        verified = [o for o in result["records"][0]["observations"]
                    if o["field"] == "standard_number" and o["status"] == "verified"]
        self.assertEqual(verified[0]["value"], "T/CMSA 0013-2019")
        self.assertTrue(verified[0]["verification_method"])

    def test_no_conflict_when_filename_matches_pdf(self):
        path = _make_pdf(self.root / "data/raw/gb.pdf", "GB 1-2026 正文")
        rows = [{
            "file_name": path.name, "rel_path": str(path.relative_to(self.root).as_posix()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": "2",
            "std_no": "GB 1-2026", "document_kind": "standard", "jurisdiction": "全国",
        }]
        self._inventory(rows)
        result = audit_sources(self.root)
        self.assertEqual(collect_standard_number_conflicts(result), [])


if __name__ == "__main__":
    unittest.main()
