"""T07 pre-fix red-light tests: recovery overrides are TEXT-STAGE inputs.

The current recovery path in ``CorpusUpdateService._page`` replaces the whole
page dict wholesale, bypassing ``UnifiedPageExtractor`` entirely -- a QXT
recovery page restores the OCR text but LOSES the unified structure pass
(regions, layout status, four content statuses, structure_execution_complete,
tables/formulas/figures processing). These tests prove the defect and pin the
contract after the fix:

1. a recovered page must still run the unified structure stages on top of the
   recovered text (structure_execution_complete, four content_types, regions
   matching the unified layout detection);
2. the text extractor must NOT re-read the (garbled) PDF for a recovered page;
3. a recovery page carrying structure fields (e.g. copied from a published
   jsonl) must be refused, never silently accepted;
4. recovery-derived pages must never enter the reusable page/document caches
   -- a later build WITHOUT --recovery-dir must not inherit recovery content.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import pymupdf

from src.application.corpus_update import (
    AutomaticPageExtractor,
    ContentAsset,
    CorpusUpdateService,
    _stable_json_sha,
    page_extractor_config_hash,
    write_recovery_pages,
)
from src.application.unified_pdf import UnifiedPageExtractor

_RECOVERED_TEXT = "恢复后的可靠正文文字，用于外科手术式恢复验证。"


class RecoveryTextOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data/raw").mkdir(parents=True)
        pdf = pymupdf.open()
        p = pdf.new_page(width=300, height=400)
        p.insert_text((20, 30), "Original garbled body text placeholder.")
        p.insert_text((20, 100), "Q = A * v (1)")
        p.draw_rect((20, 180, 150, 250))
        pdf.save(self.root / "data/raw/scanned.pdf")
        pdf.close()
        self.sha = hashlib.sha256(
            (self.root / "data/raw/scanned.pdf").read_bytes()).hexdigest()
        self.asset = ContentAsset(self.sha, "data/raw/scanned.pdf",
                                  "scanned.pdf", 1, (), {})
        # independent synthetic layout detection (4 regions, all four kinds)
        self.layout = Mock()
        boxes = [
            {"label": "text", "score": 0.95, "coordinate": [40, 30, 600, 100]},
            {"label": "formula", "score": 0.95, "coordinate": [40, 160, 350, 210]},
            {"label": "image", "score": 0.95, "coordinate": [40, 360, 300, 500]},
            {"label": "table", "score": 0.95, "coordinate": [40, 560, 300, 700]},
        ]
        self.layout.predict.side_effect = lambda image: [
            {**b, "coordinate": [b["coordinate"][0] * image.shape[1] / 600,
                                 b["coordinate"][1] * image.shape[0] / 800,
                                 b["coordinate"][2] * image.shape[1] / 600,
                                 b["coordinate"][3] * image.shape[0] / 800]}
            for b in boxes]
        self.formula = Mock()
        self.formula.predict.return_value = "Q=A v"
        self.tables = Mock()
        self.tables.parse_page.return_value = {
            "physical_page": 1, "width": 300, "height": 400, "elements": [],
            "raw": {"render_width": 600, "render_height": 800},
            "tables": [{"table_id": "table-1",
                        "html": '<table><tr><td>1.0</td></tr></table>',
                        "raw": {"bbox": [40, 560, 300, 700]}}]}
        # a spy text extractor: recovery must NOT re-read the garbled PDF
        self.text_extractor = AutomaticPageExtractor(
            project_root=self.root, enable_ocr=False)
        self.text_extractor.extract = Mock(
            side_effect=self.text_extractor.extract)

    def _write_recovery(self, extra_fields: dict | None = None):
        page = {
            "physical_page": 1, "page_index": 0, "display_page_label": None,
            "width": 300.0, "height": 400.0, "rotation": 0,
            "extraction_route": "full_ocr",
            "parser_name": "paddleocr-text", "parser_profile": "recovery",
            "decision_status": "approved", "decision_reasons": [],
            "publishable": True, "text": _RECOVERED_TEXT,
            "elements": [{"element_id": "p1-t1", "text": _RECOVERED_TEXT,
                          "bbox": [20, 16, 280, 40]}],
            "tables": [], "coordinate_normalizations": [],
        }
        page.update(extra_fields or {})
        record = write_recovery_pages(self.root, self.sha, [page])
        return page, record

    def _service(self, *, recovery: bool):
        extractor = UnifiedPageExtractor(
            self.root, self.text_extractor, layout=self.layout,
            formula=self.formula, tables=self.tables,
            model_config={"fixture": "t07-recovery"})
        return CorpusUpdateService(
            self.root, page_extractor=extractor,
            recovery_dir=self.root / "data/canonical/recovery"
            if recovery else None), extractor

    # -- 1. the core red light: structure must survive recovery ------------

    def test_recovered_page_still_runs_unified_structure(self):
        self._write_recovery()
        service, extractor = self._service(recovery=True)
        page, reused = service._page(
            self.asset, page_extractor_config_hash(extractor), 1, extractor)
        self.assertFalse(reused)
        # recovered text survives...
        self.assertEqual(page["text"], _RECOVERED_TEXT)
        # ...and the unified structure pass RAN on top of it
        self.assertIs(page.get("structure_execution_complete"), True)
        self.assertEqual(set(page.get("content_types") or {}),
                         {"text", "table", "formula", "image"})
        self.assertEqual(
            sorted(r["kind"] for r in page.get("regions") or []),
            ["formula", "image", "table", "text"])
        self.assertEqual(page.get("layout_status"), "completed")
        for region in page["regions"]:
            self.assertEqual(region["file_sha256"], self.sha)
            self.assertTrue(region["region_id"].startswith("up-"))
        formula = next(r for r in page["regions"] if r["kind"] == "formula")
        self.assertEqual(formula.get("raw_latex"), "Q=A v")

    def test_recovery_does_not_reextract_text_from_the_pdf(self):
        self._write_recovery()
        service, extractor = self._service(recovery=True)
        service._page(self.asset, page_extractor_config_hash(extractor), 1,
                      extractor)
        self.text_extractor.extract.assert_not_called()

    # -- 2. structure content in a recovery page is illegal input ----------

    def test_recovery_page_carrying_structure_fields_is_refused(self):
        # simulates copying a page out of the published jsonl: the recovered
        # text comes bundled with OLD structure -- it must fail loudly, the
        # forged structure must never flow into a new build
        forged = [{"region_id": "up-forged", "kind": "text",
                   "execution_status": "completed"}]
        self._write_recovery({
            "regions": forged, "content_types": {"text": {}},
            "structure_execution_complete": True})
        service, extractor = self._service(recovery=True)
        page, _ = service._page(
            self.asset, page_extractor_config_hash(extractor), 1, extractor)
        # the text stage failed loudly -> the page cannot be publishable
        self.assertEqual(page["decision_status"], "failed")
        # the forged structure never entered the page
        self.assertNotIn("up-forged",
                         [r.get("region_id") for r in page.get("regions", [])])

    # -- 3. recovery content never leaks into reusable caches --------------

    def test_recovery_derived_pages_never_enter_reusable_caches(self):
        self._write_recovery()
        service, extractor = self._service(recovery=True)
        key = page_extractor_config_hash(extractor)
        service._page(self.asset, key, 1, extractor)
        # neither the page cache nor the document cache may hold the
        # recovery-derived page -- a later build without --recovery-dir must
        # not silently inherit recovered content
        self.assertFalse(service._page_cache_path(self.asset, key, 1).is_file(),
                         "recovery-derived page leaked into the page cache")
        self.assertFalse(service._cache_path(self.asset, key).is_file(),
                         "recovery-derived document leaked into the doc cache")
        # and a build WITHOUT the recovery dir re-derives from the PDF
        plain, plain_extractor = self._service(recovery=False)
        page2, reused2 = plain._page(
            self.asset, page_extractor_config_hash(plain_extractor), 1,
            plain_extractor)
        self.assertFalse(reused2)
        self.assertNotEqual(page2["text"], _RECOVERED_TEXT)

    def test_recovery_text_source_is_machine_independent(self):
        # Finding A: the recovery provenance enters the document -> the
        # corpus_version hash, so it must be a RELATIVE posix path. An
        # absolute path (drive letter + backslashes) would make the same
        # recovery content derive different corpus versions on different
        # machines/checkout paths -- breaking content-identity of the
        # corpus version id.
        self._write_recovery()
        service, extractor = self._service(recovery=True)
        page, _ = service._page(
            self.asset, page_extractor_config_hash(extractor), 1, extractor)
        source = page.get("recovery_text_source") or {}
        recovery_dir = source.get("recovery_dir")
        self.assertIsInstance(recovery_dir, str)
        self.assertNotIn("\\", recovery_dir, "backslash in recovery_dir")
        self.assertNotRegex(recovery_dir, r"^[A-Za-z]:", "drive letter in recovery_dir")
        self.assertFalse(Path(recovery_dir).is_absolute())
        self.assertEqual(recovery_dir, "data/canonical/recovery")
        self.assertEqual(source.get("page_file"), "0001.json")
        self.assertTrue(source.get("checkpoint_sha256"))

    def test_unrelativizable_recovery_dir_fails_closed(self):
        # a recovery dir OUTSIDE the project root cannot be expressed as a
        # project-relative posix path -> refuse the build input, never fall
        # back to an absolute path
        import shutil as _shutil
        import tempfile as _tf
        outside = Path(_tf.mkdtemp(prefix="t07-outside-recovery-"))
        self.addCleanup(_shutil.rmtree, outside, ignore_errors=True)
        page = {
            "physical_page": 1, "page_index": 0, "display_page_label": None,
            "width": 300.0, "height": 400.0, "rotation": 0,
            "extraction_route": "full_ocr",
            "parser_name": "paddleocr-text", "parser_profile": "recovery",
            "decision_status": "approved", "decision_reasons": [],
            "publishable": True, "text": _RECOVERED_TEXT,
            "elements": [{"element_id": "p1-t1", "text": _RECOVERED_TEXT,
                          "bbox": [20, 16, 280, 40]}],
            "tables": [], "coordinate_normalizations": [],
        }
        recovery_root = outside / "recovery"
        (recovery_root / self.sha).mkdir(parents=True)
        (recovery_root / self.sha / "0001.json").write_text(
            json.dumps({**page, "checkpoint_sha256": _stable_json_sha(page)}),
            encoding="utf-8")
        extractor = UnifiedPageExtractor(
            self.root, self.text_extractor, layout=self.layout,
            formula=self.formula, tables=self.tables,
            model_config={"fixture": "t07-recovery"})
        service = CorpusUpdateService(
            self.root, page_extractor=extractor, recovery_dir=recovery_root)
        with self.assertRaises(ValueError):
            service._page(
                self.asset, page_extractor_config_hash(extractor), 1, extractor)

    def test_document_cache_write_is_skipped_while_recovery_is_active(self):
        registry = self.root / "data/registry"
        registry.mkdir(parents=True, exist_ok=True)
        with (registry / "inventory.csv").open("w", encoding="utf-8",
                                               newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["file_name", "rel_path", "sha256", "pages"])
            writer.writeheader()
            writer.writerow({"file_name": "scanned.pdf",
                             "rel_path": "data/raw/scanned.pdf",
                             "sha256": self.sha, "pages": 1})
        self._write_recovery()
        service, extractor = self._service(recovery=True)
        result = service.build()
        self.assertEqual(result["page_count"], 1)
        key = page_extractor_config_hash(extractor)
        self.assertFalse((self.root / "data/canonical" / "assets"
                          / f"{self.sha}.{key[:12]}.json").is_file())
        # a follow-up build without recovery re-derives the page from the PDF
        plain, _ = self._service(recovery=False)
        plain_result = plain.build()
        self.assertEqual(plain_result["page_count"], 1)
        page = json.loads(
            (self.root / "data/canonical"
             / f"{plain_result['corpus_version']}-documents.jsonl"
             ).read_text(encoding="utf-8"))["pages"][0]
        self.assertNotEqual(page["text"], _RECOVERED_TEXT)


    def test_registry_manifest_records_recovery_provenance(self):
        # Finding B: the registry manifest alone must prove the recovery
        # channel participated -- canonical relative dir + per-page digests
        registry = self.root / "data/registry"
        registry.mkdir(parents=True, exist_ok=True)
        with (registry / "inventory.csv").open("w", encoding="utf-8",
                                               newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["file_name", "rel_path", "sha256", "pages"])
            writer.writeheader()
            writer.writerow({"file_name": "scanned.pdf",
                             "rel_path": "data/raw/scanned.pdf",
                             "sha256": self.sha, "pages": 1})
        page, _ = self._write_recovery()
        service, extractor = self._service(recovery=True)
        result = service.build()
        manifest = json.loads(
            (registry / f"{result['corpus_version']}.json"
             ).read_text(encoding="utf-8"))
        provenance = manifest.get("recovery_provenance") or {}
        self.assertEqual(provenance.get("schema_version"),
                         "t07-recovery-manifest-1")
        self.assertEqual(provenance.get("recovery_dir"),
                         "data/canonical/recovery")
        docs = provenance.get("documents") or []
        self.assertEqual([d["sha256"] for d in docs], [self.sha])
        self.assertEqual(docs[0]["pages"][0]["physical_page"], 1)
        self.assertEqual(docs[0]["pages"][0]["checkpoint_sha256"],
                         _stable_json_sha(page))
        # machine-independence: the manifest never carries an absolute
        # recovery path either
        self.assertNotIn("\\", json.dumps(provenance))


    # ---- T07-Precondition R3: recovery input closure + candidate immutability

    @staticmethod
    def _tree(root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}

    def _write_inventory(self):
        registry = self.root / "data/registry"
        registry.mkdir(parents=True, exist_ok=True)
        with (registry / "inventory.csv").open("w", encoding="utf-8",
                                               newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["file_name", "rel_path", "sha256", "pages"])
            writer.writeheader()
            writer.writerow({"file_name": "scanned.pdf",
                             "rel_path": "data/raw/scanned.pdf",
                             "sha256": self.sha, "pages": 1})

    def _assert_zero_writes(self, before: set[str]):
        self.assertEqual(self._tree(self.root), before,
                         "a failed recovery build must not write ANY file")

    def test_empty_recovery_dir_hard_fails_with_zero_writes(self):
        self._write_inventory()
        (self.root / "data/canonical/recovery").mkdir(parents=True)
        service, _ = self._service(recovery=True)
        before = self._tree(self.root)
        with self.assertRaises(ValueError):
            service.build()
        self._assert_zero_writes(before)

    def test_recovery_record_and_page_inconsistencies_hard_fail(self):
        self._write_inventory()
        mutations = {
            "missing_record": lambda d: (d / "recovery_record.json").unlink(),
            "record_page_missing_file": lambda d: (d / "0001.json").unlink(),
            "record_sha_mismatch": lambda d: (d / "recovery_record.json")
                .write_text(json.dumps({
                    "schema_version": "t06-recovery-1",
                    "sha256": "f" * 64,
                    "recovery_dir": "data/canonical/recovery",
                    "pages": [1]}), encoding="utf-8"),
            "out_of_range_page": lambda d: (d / "0099.json").write_text(
                (d / "0001.json").read_text(encoding="utf-8"),
                encoding="utf-8"),
            "extra_page_without_record_entry": lambda d: (
                d / "0002.json").write_text(
                (d / "0001.json").read_text(encoding="utf-8"),
                encoding="utf-8"),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label):
                # start each mutation from the pristine recovery dir
                shutil.rmtree(self.root / "data/canonical/recovery",
                              ignore_errors=True)
                self._write_recovery()
                mutate(self.root / "data/canonical/recovery" / self.sha)
                service, _ = self._service(recovery=True)
                before = self._tree(self.root)
                with self.assertRaises(ValueError):
                    service.build()
                self._assert_zero_writes(before)

    def test_recovery_nonempty_tables_hard_fails_with_zero_writes(self):
        self._write_inventory()
        self._write_recovery({
            "tables": [{"table_id": "t1", "html": "<table><tr><td>x</td></tr>"
                        "</table>", "cells": [{"row": 0, "col": 0,
                                               "text": "x"}]}]})
        service, _ = self._service(recovery=True)
        before = self._tree(self.root)
        with self.assertRaises(ValueError):
            service.build()
        self._assert_zero_writes(before)

    def test_conflicting_candidate_outputs_are_never_overwritten(self):
        self._write_inventory()
        self._write_recovery()
        service, _ = self._service(recovery=True)
        first = service.build()
        cv = first["corpus_version"]
        jsonl = self.root / "data/canonical" / f"{cv}-documents.jsonl"
        audit = self.root / "data/canonical" / f"{cv}-structure-audit.json"
        manifest = self.root / "data/registry" / f"{cv}.json"
        originals = {p: p.read_bytes() for p in (jsonl, audit, manifest)}
        from src.application.publish_state import CandidateConflictError
        for target in (jsonl, audit, manifest):
            tampered = b"TAMPERED-" + originals[target]
            target.write_bytes(tampered)
            try:
                with self.assertRaises(CandidateConflictError):
                    service.build()
            finally:
                # the rerun must leave the tampered bytes EXACTLY as they
                # were -- a rebuild never truncates or overwrites a candidate
                self.assertEqual(target.read_bytes(), tampered)
            target.write_bytes(originals[target])
        # final sanity: with pristine candidates the rerun reuses quietly
        service.build()
        self.assertEqual(jsonl.read_bytes(), originals[jsonl])
        self.assertEqual(audit.read_bytes(), originals[audit])
        self.assertEqual(manifest.read_bytes(), originals[manifest])

    def test_same_recovery_content_same_version_across_checkout_paths(self):
        import hashlib as _hashlib
        roots = []
        versions = []
        payloads = []
        try:
            for _ in range(2):
                import tempfile as _tf
                tmp = _tf.mkdtemp(prefix="t07-checkout-")
                roots.append(tmp)
                root = Path(tmp)
                (root / "data/raw").mkdir(parents=True)
                (root / "data/raw/scanned.pdf").write_bytes(
                    (self.root / "data/raw/scanned.pdf").read_bytes())
                sha = _hashlib.sha256(
                    (root / "data/raw/scanned.pdf").read_bytes()).hexdigest()
                (root / "data/registry").mkdir(parents=True)
                with (root / "data/registry/inventory.csv").open(
                        "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(
                        f, fieldnames=["file_name", "rel_path", "sha256",
                                       "pages"])
                    writer.writeheader()
                    writer.writerow({"file_name": "scanned.pdf",
                                     "rel_path": "data/raw/scanned.pdf",
                                     "sha256": sha, "pages": 1})
                page = {
                    "physical_page": 1, "page_index": 0,
                    "display_page_label": None,
                    "width": 300.0, "height": 400.0, "rotation": 0,
                    "extraction_route": "full_ocr",
                    "parser_name": "paddleocr-text",
                    "parser_profile": "recovery",
                    "decision_status": "approved", "decision_reasons": [],
                    "publishable": True, "text": _RECOVERED_TEXT,
                    "elements": [{"element_id": "p1-t1",
                                  "text": _RECOVERED_TEXT,
                                  "bbox": [20, 16, 280, 40]}],
                    "tables": [], "coordinate_normalizations": [],
                }
                write_recovery_pages(root, sha, [page])
                extractor = UnifiedPageExtractor(
                    root, self.text_extractor, layout=self.layout,
                    formula=self.formula, tables=self.tables,
                    model_config={"fixture": "t07-recovery"})
                service = CorpusUpdateService(
                    root, page_extractor=extractor,
                    recovery_dir=root / "data/canonical/recovery")
                result = service.build()
                versions.append(result["corpus_version"])
                payloads.append((root / "data/canonical"
                                 / f"{result['corpus_version']}-documents.jsonl"
                                 ).read_bytes())
            self.assertEqual(versions[0], versions[1],
                             "same recovery content must derive the SAME "
                             "corpus version regardless of checkout path")
            self.assertEqual(payloads[0], payloads[1])
        finally:
            import shutil as _shutil
            for tmp in roots:
                _shutil.rmtree(tmp, ignore_errors=True)

    def test_changed_recovery_content_derives_new_version(self):
        self._write_inventory()
        self._write_recovery()
        service, _ = self._service(recovery=True)
        first = service.build()
        first_version = first["corpus_version"]
        first_jsonl = (self.root / "data/canonical"
                       / f"{first_version}-documents.jsonl").read_bytes()
        # change ONLY the recovered text -> the candidate version must move
        changed = dict(_RECOVERED_TEXT="恢复内容第二版，与第一版不同。")
        record = json.loads(
            (self.root / "data/canonical/recovery" / self.sha
             / "recovery_record.json").read_text(encoding="utf-8"))
        page_path = (self.root / "data/canonical/recovery" / self.sha
                     / "0001.json")
        page = json.loads(page_path.read_text(encoding="utf-8"))
        page.pop("checkpoint_sha256")
        page["text"] = changed["_RECOVERED_TEXT"]
        page["elements"][0]["text"] = changed["_RECOVERED_TEXT"]
        write_recovery_pages(self.root, self.sha, [page])
        self.assertEqual(
            json.loads((self.root / "data/canonical/recovery" / self.sha
                        / "recovery_record.json").read_text(
                            encoding="utf-8"))["pages"], [1])
        second = service.build()
        self.assertNotEqual(second["corpus_version"], first_version)
        self.assertNotEqual(
            (self.root / "data/canonical"
             / f"{second['corpus_version']}-documents.jsonl").read_bytes(),
            first_jsonl)
        # the old candidate is preserved untouched
        self.assertEqual(
            (self.root / "data/canonical"
             / f"{first_version}-documents.jsonl").read_bytes(),
            first_jsonl)


if __name__ == "__main__":
    unittest.main()
