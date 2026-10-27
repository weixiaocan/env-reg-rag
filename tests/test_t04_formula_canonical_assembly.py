"""T04 -- formula region & cache strict reconciliation.

Every DETECTED formula region (page-intermediate ground truth) must close with
exactly ONE outcome and, when completed, with exactly one FormulaElement.
Missing / extra / duplicate / wrong-page / wrong-config / corrupt-JSON /
gate-rejected regions are never silently skipped. Mirrors the frozen T03
outcome-ledger contracts with kind="formula" (D1: execution and quality
statuses are disjoint).

Run::

    .venv/Scripts/python.exe -m pytest tests/test_t04_formula_canonical_assembly.py -q
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from typing import Any
import unittest

from src.application.canonical_assembly import (
    assemble_document,
    load_formula_pages,
)
from src.application.canonical_validation import compute_ids, validate_document
from src.domain.canonical_document import CanonicalDocument, SchemaError

_DUMMY_SHA = "b" * 64
_RUN_ID = "f04f04f04f04f04f"
_CONFIG_HASH = _RUN_ID + "a" * 48
_PROFILE = "layout-formula-v1:200dpi:score0.5"
_BBOX = [100.0, 200.0, 400.0, 260.0]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

_DEFAULT_BBOX = object()  # sentinel: use the standard fixture bbox


def _pi_formula_region(
    region_id: str = "up-formula00000000000001",
    bbox: Any = _DEFAULT_BBOX,
    *,
    latex: str | None = "T = \\sum p_j",
    issues: list[str] | None = None,
    relations: list[dict] | None = None,
    exec_status: str = "completed",
) -> dict:
    return {
        "region_id": region_id, "element_id": region_id, "kind": "formula",
        "bbox": list(_BBOX) if bbox is _DEFAULT_BBOX else bbox,
        "execution_status": exec_status, "quality_status": "needs_review",
        "issues": list(issues or []), "relations": list(relations or []),
        "physical_page": 1,
        "processing_config_id": "unified-local-v5:cfg",
        **({} if latex is None else {"raw_latex": latex}),
    }


def _host_text_region(bbox: list[float], text: str = "其中 T = Σ p j 为合计值") -> dict:
    """A text block covering the formula whose text holds its characters."""
    return {"_host_element": {"element_id": "p1-host", "text": text, "bbox": list(bbox)}}


def _cache_region(
    bbox: list[float] | None = None,
    *,
    latex: str | None = "T = \\sum p_j",
    score: float | None = 0.9,
    candidate_id: str = "layout-x-0",
    page: int = 1,
) -> dict:
    return {
        "kind": "formula", "bbox": bbox if bbox is not None else list(_BBOX),
        "score": score, "raw_latex": latex, "physical_page": page,
        "candidate_id": candidate_id,
        "crop_bbox": [98.0, 198.0, 402.0, 262.0],
        "review_status": "pending_review",
    }


def _cache_file(sha: str, page: int, regions: list[dict]) -> dict:
    return {
        "sha256": sha, "physical_page": page, "config_hash": _CONFIG_HASH,
        "status": "completed", "publishable": False,
        "can_use_for_calculation": False,
        "result": {
            "profile": _PROFILE,
            "formula_model": "paddleocr-formula:PP-FormulaNet_plus-M",
            "package_versions": {"paddleocr": "3.7.0"},
            "pages": [{"physical_page": page, "regions": regions}],
        },
    }


def _write_cache(cache_dir: str, sha: str, files: dict[int, list[dict]]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    for page, regions in files.items():
        payload = _cache_file(sha, page, regions)
        path = os.path.join(cache_dir, f"{sha}-{page}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)


def _page(rows: list[dict], pno: int = 1) -> dict:
    hosts = [r["_host_element"] for r in rows if "_host_element" in r]
    return {
        "physical_page": pno, "width": 595.0, "height": 842.0, "rotation": 0,
        "extraction_route": "full_ocr", "decision_status": "approved",
        "decision_reasons": [], "parser_name": "paddleocr-text",
        "elements": [{"element_id": f"p{pno}-t1",
                      "text": "式中 L 为长度，取值见表 3。",
                      "bbox": [60.0, 300.0, 500.0, 320.0]}] + hosts,
        "regions": [r for r in rows if "_host_element" not in r], "tables": [],
    }


def _page_doc(pages: list[dict], *, sha: str = _DUMMY_SHA) -> dict:
    return {
        "sha256": sha, "file_name": "synthetic-t04.pdf",
        "config_hash": "c" * 64, "processing_run_id": "run_test",
        "metadata": {"document_kind": "standard"}, "pages": pages,
    }


def _load(cache_dir: str, sha: str = _DUMMY_SHA, pages: range = range(1, 2)):
    return load_formula_pages(sha, pages, cache_dir=cache_dir)


def _assemble(page_doc: dict, formula_pages) -> dict:
    doc = assemble_document(page_doc, formula_pages=formula_pages)
    ids = compute_ids(doc)
    doc.update(ids)
    validate_document(doc)
    return doc


def _formula_outcomes(doc: dict) -> list[dict]:
    return [o for o in doc.get("structured_outcomes") or []
            if o.get("kind") == "formula"]


def _formula_elements(doc: dict) -> list[dict]:
    return [e for e in doc.get("elements") or [] if e.get("type") == "formula"]


# --------------------------------------------------------------------------- #
# 1. Strict cache loading
# --------------------------------------------------------------------------- #

class TestStrictLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t04-cache-")
        self.cache_dir = os.path.join(self.tmp, _RUN_ID)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_cache_dir_hard_fails(self):
        with self.assertRaises(FileNotFoundError):
            _load(os.path.join(self.tmp, "nonexistent"))

    def test_happy_path_returns_regions_and_no_failures(self):
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: [_cache_region()]})
        pages, failures = _load(self.cache_dir)
        self.assertEqual(failures, [])
        self.assertEqual(len(pages[1]), 1)
        region = pages[1][0]
        self.assertEqual(region["raw_latex"], "T = \\sum p_j")
        # file-level provenance annotation (config/engine) is preserved
        self.assertEqual(region["_formula_run"]["profile"], _PROFILE)
        self.assertEqual(region["_formula_run"]["config_hash"], _CONFIG_HASH)
        self.assertEqual(region["_formula_run"]["paddleocr_version"], "3.7.0")

    def test_corrupt_json_hard_fails(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not valid json!!")
        with self.assertRaises(ValueError) as ctx:
            _load(self.cache_dir)
        self.assertIn("corrupt", str(ctx.exception))

    def test_wrong_sha_hard_fails(self):
        # the file EXISTS under this sha's name but claims another sha inside
        os.makedirs(self.cache_dir, exist_ok=True)
        payload = _cache_file(_DUMMY_SHA, 1, [])
        payload["sha256"] = "d" * 64
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError) as ctx:
            _load(self.cache_dir)
        self.assertIn("sha256", str(ctx.exception))

    def test_wrong_config_hard_fails(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        payload = _cache_file(_DUMMY_SHA, 1, [])
        payload["config_hash"] = "e" * 64  # not this run's config
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError) as ctx:
            _load(self.cache_dir)
        self.assertIn("config_hash", str(ctx.exception))

    def test_wrong_page_in_cache_file_hard_fails(self):
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: []})
        os.makedirs(self.cache_dir, exist_ok=True)
        # region claims page 7 inside the page-1 file
        payload = _cache_file(_DUMMY_SHA, 1, [_cache_region()])
        payload["result"]["pages"][0]["regions"][0]["physical_page"] = 7
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError) as ctx:
            _load(self.cache_dir)
        self.assertIn("physical_page", str(ctx.exception))

    def test_non_formula_region_hard_fails(self):
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: []})
        payload = _cache_file(_DUMMY_SHA, 1, [_cache_region()])
        payload["result"]["pages"][0]["regions"][0]["kind"] = "text"
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError):
            _load(self.cache_dir)

    def test_invalid_region_coordinates_hard_fails(self):
        payload = _cache_file(_DUMMY_SHA, 1, [_cache_region()])
        payload["result"]["pages"][0]["regions"][0]["bbox"] = [300.0, 200.0, 100.0, 260.0]
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: []})
        with open(os.path.join(self.cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError):
            _load(self.cache_dir)

    def test_missing_page_file_is_an_auditable_failure(self):
        _write_cache(self.cache_dir, _DUMMY_SHA, {2: []})
        pages, failures = _load(self.cache_dir, pages=range(1, 3))
        self.assertNotIn(1, pages)
        self.assertEqual(failures, [{"physical_page": 1,
                                     "reason": "formula_cache_file_missing"}])


# --------------------------------------------------------------------------- #
# 2. Full match / three-way reconciliation
# --------------------------------------------------------------------------- #

class TestFullMatch(unittest.TestCase):
    def test_full_match_outcomes_and_elements(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        page_doc = _page_doc([_page([d1, d2])])
        pages, failures = _load_cache_two(d2)
        self.assertEqual(failures, [])
        doc = _assemble(page_doc, pages)
        outcomes = _formula_outcomes(doc)
        elements = _formula_elements(doc)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(len(elements), 2)
        for o in outcomes:
            self.assertEqual(o["execution_status"], "completed")
            self.assertEqual(o["quality_status"], "needs_review")  # D1 default
            self.assertEqual(o["selection_basis"], "single_admissible")
            self.assertEqual(len(o["attempts"]), 1)
            attempt = o["attempts"][0]
            self.assertTrue(attempt["selected"])
            self.assertEqual(attempt["route"], "formula_crop_ocr")
            self.assertEqual(attempt["execution_status"], "completed")
            self.assertEqual(attempt["result_id"], o["selected_result_id"])
        self.assertEqual(
            {o["element_id"] for o in outcomes},
            {e["element_id"] for e in elements})
        # T04-R2: the SOURCE region_id is the verbatim upstream region_id --
        # separate from the recognition RESULT identity (fx-*), which is what
        # admissible/selected bind to.
        self.assertEqual(
            {o["region_id"] for o in outcomes}, {"up-f1", "up-f2"})
        for o in outcomes:
            self.assertTrue(o["selected_result_id"].startswith("fx-"))
            self.assertEqual(o["admissible_result_ids"],
                             [o["selected_result_id"]])
            self.assertNotEqual(o["region_id"], o["selected_result_id"])
        # the element traces back to the original detected regions verbatim
        self.assertEqual(
            {e["source_region_ids"][0] for e in elements}, {"up-f1", "up-f2"})

    def test_region_identity_ignores_candidate_id_and_order(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        page_doc = _page_doc([_page([d1, d2])])
        pages, _ = _load_cache_two(d2)
        base = _assemble(page_doc, pages)
        # the same regions in REVERSED input order assemble identically
        rev_doc = _page_doc([_page([d1, d2])])
        rev_doc["pages"][0]["regions"] = list(reversed(rev_doc["pages"][0]["regions"]))
        cache_rev = {p: list(reversed(rs)) for p, rs in pages.items()}
        flipped = _assemble(rev_doc, cache_rev)
        self.assertEqual(
            {o["region_id"] for o in _formula_outcomes(base)},
            {o["region_id"] for o in _formula_outcomes(flipped)})
        # the derived fx RESULT identity is also order-independent
        self.assertEqual(
            {o["selected_result_id"] for o in _formula_outcomes(base)},
            {o["selected_result_id"] for o in _formula_outcomes(flipped)})
        self.assertNotIn("layout-x-0",
                         json.dumps(_formula_outcomes(base)))  # not array position


def _load_cache_two(d2):
    """Loader-based cache for TestFullMatch (real temp dir, 2 regions)."""
    tmp = tempfile.mkdtemp(prefix="t04-full-")
    cache_dir = os.path.join(tmp, _RUN_ID)
    _write_cache(cache_dir, _DUMMY_SHA, {
        1: [_cache_region(candidate_id="layout-x-0"),
            _cache_region(bbox=[100.0, 300.0, 400.0, 360.0],
                          latex="L = 2.0 h", candidate_id="layout-x-1")],
    })
    pages, failures = _load(cache_dir)
    return pages, failures


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t04-recon-")
        self.cache_dir = os.path.join(self.tmp, _RUN_ID)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _assemble_with_cache(self, pi_regions: list[dict], files: dict):
        _write_cache(self.cache_dir, _DUMMY_SHA, files)
        pages, _ = _load(self.cache_dir, pages=range(1, 3))
        page_doc = _page_doc([_page(pi_regions)])
        return _assemble(page_doc, pages)

    def test_missing_one_region_closes_failed(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        doc = self._assemble_with_cache(
            [d1, d2], {1: [_cache_region()]})  # cache has only d1's result
        outcomes = _formula_outcomes(doc)
        by_status = {o["execution_status"] for o in outcomes}
        self.assertEqual(len(outcomes), 2)
        self.assertIn("failed", by_status)
        self.assertIn("completed", by_status)
        failed = [o for o in outcomes if o["execution_status"] == "failed"][0]
        self.assertIn("formula_cache_result_missing", failed["issues"])
        self.assertEqual(failed["element_id"], None)
        self.assertEqual(len(_formula_elements(doc)), 1)
        # the failed outcome still carries a bound region-level attempt
        self.assertEqual(len(failed["attempts"]), 1)
        self.assertEqual(failed["attempts"][0]["result_id"], None)
        self.assertEqual(failed["attempts"][0]["failure_reason"],
                         "formula_cache_result_missing")

    def test_extra_cache_region_hard_fails(self):
        d1 = _pi_formula_region("up-f1")
        with self.assertRaises(ValueError) as ctx:
            self._assemble_with_cache(
                [d1], {1: [_cache_region(),
                           _cache_region(bbox=[10.0, 20.0, 30.0, 40.0],
                                         latex="x = y")]})
        self.assertIn("without a detected source region", str(ctx.exception))

    def test_extra_cache_page_hard_fails(self):
        d1 = _pi_formula_region("up-f1")
        # the cache carries a region on page 2, which the page-intermediate
        # (1 page) has never detected -- must hard-fail, not be dropped
        with self.assertRaises(ValueError) as ctx:
            self._assemble_with_cache(
                [d1], {1: [_cache_region()],
                       2: [_cache_region(bbox=[10.0, 20.0, 30.0, 40.0],
                                         candidate_id="x-2", page=2)]})
        self.assertIn("without a detected source region", str(ctx.exception))

    def test_duplicate_cache_regions_hard_fail(self):
        d1 = _pi_formula_region("up-f1")
        with self.assertRaises(ValueError) as ctx:
            self._assemble_with_cache(
                [d1], {1: [_cache_region(), _cache_region()]})
        self.assertIn("duplicate", str(ctx.exception))

    def test_duplicate_detected_regions_hard_fail(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2")  # same bbox as d1
        with self.assertRaises(ValueError):
            self._assemble_with_cache([d1, d2], {1: [_cache_region()]})

    def test_wrong_bbox_cache_region_hard_fails(self):
        d1 = _pi_formula_region("up-f1")
        # cache bbox drifted far beyond the match tolerance
        drifted = [v + 5.0 for v in _BBOX]
        with self.assertRaises(ValueError):
            self._assemble_with_cache([d1], {1: [_cache_region(bbox=drifted)]})

    def test_all_failures_produce_no_elements_but_validate(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        doc = self._assemble_with_cache(
            [d1, d2],
            {1: [_cache_region(latex=None), _cache_region(bbox=[100.0, 300.0, 400.0, 360.0],
                                                          latex=None)]})
        self.assertEqual(len(_formula_outcomes(doc)), 2)
        self.assertEqual(len(_formula_elements(doc)), 0)
        for o in _formula_outcomes(doc):
            self.assertEqual(o["execution_status"], "failed")
            self.assertIn("empty_formula_recognition", o["issues"])
            self.assertEqual(o["attempts"][0]["execution_status"], "failed")

    def test_cache_file_missing_entirely_all_failed(self):
        _write_cache(self.cache_dir, _DUMMY_SHA, {})  # dir exists, no files
        pages, failures = _load(self.cache_dir)
        self.assertEqual(pages, {})
        page_doc = _page_doc([_page([_pi_formula_region("up-f1")])])
        doc = _assemble(page_doc, pages)
        outcomes = _formula_outcomes(doc)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["execution_status"], "failed")
        self.assertIn("formula_cache_result_missing", outcomes[0]["issues"])
        self.assertEqual(len(_formula_elements(doc)), 0)

    def test_gate_threshold_region_unsupported_not_silent(self):
        d1 = _pi_formula_region("up-f1")
        doc = self._assemble_with_cache(
            [d1, _host_text_region(_BBOX)], {1: [_cache_region(score=0.4)]})
        outcomes = _formula_outcomes(doc)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["execution_status"], "unsupported")
        self.assertTrue(any(i.startswith("formula_below_gate_threshold:score")
                            for i in outcomes[0]["issues"]))
        self.assertEqual(len(_formula_elements(doc)), 0)
        # the successful recognition is still recorded, unselected
        attempt = outcomes[0]["attempts"][0]
        self.assertEqual(attempt["execution_status"], "completed")
        self.assertFalse(attempt["selected"])

    def test_area_gate_threshold_region_unsupported(self):
        tiny = [100.0, 200.0, 110.0, 208.0]  # 10*8 = 80 < 500
        d1 = _pi_formula_region("up-f1", bbox=list(tiny))
        doc = self._assemble_with_cache(
            [d1, _host_text_region([90.0, 195.0, 400.0, 215.0])],
            {1: [_cache_region(bbox=tiny, score=0.95)]})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "unsupported")
        self.assertTrue(any(i.startswith("formula_below_gate_threshold:area")
                            and i.endswith("kept_by=text:content")
                            for i in outcome["issues"]))

    def test_small_standalone_formula_is_kept(self):
        # "t=t1+t2" on its own line: small, but no text region holds it, and
        # block assembly never emits lines inside a formula region.
        tiny = [172.0, 473.0, 214.0, 483.0]  # 42*10 = 420 < 500
        d1 = _pi_formula_region("up-f1", bbox=list(tiny))
        doc = self._assemble_with_cache(
            [d1], {1: [_cache_region(bbox=tiny, score=0.9, latex="t=t_{1}+t_{2}")]})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "completed")
        self.assertEqual(len(_formula_elements(doc)), 1)

    def test_covering_text_without_the_formula_does_not_host(self):
        # The text stage dropped the formula line: a block covers the spot
        # but its text does not hold the formula, so the formula is kept.
        tiny = [100.0, 200.0, 110.0, 208.0]
        d1 = _pi_formula_region("up-f1", bbox=list(tiny))
        doc = self._assemble_with_cache(
            [d1, _host_text_region([90.0, 195.0, 400.0, 215.0], text="雨水管渠的降雨历时应按下式计算")],
            {1: [_cache_region(bbox=tiny, score=0.95)]})
        self.assertEqual(_formula_outcomes(doc)[0]["execution_status"], "completed")

    def test_low_score_standalone_formula_is_kept(self):
        d1 = _pi_formula_region("up-f1")
        doc = self._assemble_with_cache([d1], {1: [_cache_region(score=0.4)]})
        self.assertEqual(_formula_outcomes(doc)[0]["execution_status"], "completed")

    def test_gate_boundary_values_complete(self):
        # score exactly 0.6, area exactly 500 -> admitted
        edge = [100.0, 200.0, 150.0, 210.0]  # 50*10 = 500
        d1 = _pi_formula_region("up-f1", bbox=list(edge))
        doc = self._assemble_with_cache(
            [d1], {1: [_cache_region(bbox=edge, score=0.6)]})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "completed")

    def test_bbox_none_detected_region_unsupported(self):
        d1 = _pi_formula_region("up-f1", bbox=None)
        doc = self._assemble_with_cache([d1], {})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "unsupported")
        self.assertIn("invalid_detection_coordinates", outcome["issues"])
        self.assertEqual(len(_formula_elements(doc)), 0)

    def test_page_not_parsed_pending_closes_failed(self):
        page = _page([_pi_formula_region("up-f1")])
        page["decision_status"] = "failed"
        page["extraction_route"] = "failed"
        page["elements"] = []
        doc = _assemble(_page_doc([page]), {1: [_cache_region()]})
        outcomes = _formula_outcomes(doc)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["execution_status"], "failed")
        self.assertIn("formula_page_not_parsed", outcomes[0]["issues"])
        self.assertEqual(len(_formula_elements(doc)), 0)

    def test_pi_issues_preserved_verbatim(self):
        issues = ["formula_transcription_and_context_not_verified",
                  "formula_number_not_verified"]
        d1 = _pi_formula_region("up-f1", issues=issues)
        doc = self._assemble_with_cache([d1], {1: [_cache_region()]})
        outcome = _formula_outcomes(doc)[0]
        for iss in issues:
            self.assertIn(iss, outcome["issues"])

    def test_engine_and_config_faithful(self):
        d1 = _pi_formula_region("up-f1")
        doc = self._assemble_with_cache([d1], {1: [_cache_region()]})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["processing_config_id"], _PROFILE)
        attempt = outcome["attempts"][0]
        self.assertEqual(attempt["engine_name"], "PP-FormulaNet_plus-M")
        self.assertEqual(attempt["engine_version"], "3.7.0")
        self.assertEqual(attempt["config_id"], _PROFILE)
        self.assertEqual(attempt["config_hash"], _CONFIG_HASH)
        self.assertTrue(attempt["output_identity"].startswith("latex-sha256:"))
        self.assertEqual(attempt["confidence"], 0.9)

    def test_real_crop_asset_binds_attempt_input_identity(self):
        d1 = _pi_formula_region("up-f1")
        d1["image_ref"] = "data/model_runtime/unified_pdf/x/1/up-f1.png"
        d1["image_sha256"] = "a" * 64
        doc = self._assemble_with_cache([d1], {1: [_cache_region()]})
        outcome = _formula_outcomes(doc)[0]
        attempt = outcome["attempts"][0]
        self.assertEqual(attempt["input_identity"], f"asset-sha256:{'a' * 64}")
        el = _formula_elements(doc)[0]
        self.assertEqual(el["crop_assets"],
                         [{"ref": d1["image_ref"], "sha256": "a" * 64}])
        self.assertNotIn("missing_crop_asset", el["quality_issues"])

    def test_missing_crop_asset_is_explicit_not_fabricated(self):
        d1 = _pi_formula_region("up-f1")  # no image_ref / image_sha256
        doc = self._assemble_with_cache([d1], {1: [_cache_region()]})
        outcome = _formula_outcomes(doc)[0]
        self.assertIn("missing_crop_asset", outcome["issues"])
        attempt = outcome["attempts"][0]
        self.assertIsNone(attempt["input_identity"])
        el = _formula_elements(doc)[0]
        self.assertEqual(el["crop_assets"], [])
        self.assertIn("missing_crop_asset", el["quality_issues"])
        # a bbox is never claimed as a crop identity
        self.assertNotIn("crop-pt:", json.dumps(outcome))


# --------------------------------------------------------------------------- #
# 3. Cross-page parameters / conditions (explicit relations only)
# --------------------------------------------------------------------------- #

class TestCrossPageParams(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t04-xpage-")
        self.cache_dir = os.path.join(self.tmp, _RUN_ID)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explicit_relation_recorded_quality_stays_needs_review(self):
        d1 = _pi_formula_region(
            "up-f1",
            relations=[{"kind": "explicit_numbered_formula_parameter_reference",
                        "formula_number": "5.4.2-2",
                        "source": {"physical_page": 3},
                        "verified": False}])
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: [_cache_region()]})
        pages, _ = _load(self.cache_dir)
        doc = _assemble(_page_doc([_page([d1])]), pages)
        outcome = _formula_outcomes(doc)[0]
        self.assertTrue(any(i.startswith("cross_page_parameter_relation_explicit")
                            and ":from_page=3" in i
                            for i in outcome["issues"]))
        self.assertEqual(outcome["quality_status"], "needs_review")
        self.assertEqual(_formula_elements(doc)[0]["quality_status"], "needs_review")

    def test_uncertain_relation_recorded_never_promoted(self):
        d1 = _pi_formula_region(
            "up-f1",
            relations=[{"kind": "uncertain_next_page_parameter_marker",
                        "source": {"physical_page": 2},
                        "verified": False, "association_status": "uncertain"}])
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: [_cache_region()]})
        pages, _ = _load(self.cache_dir)
        doc = _assemble(_page_doc([_page([d1])]), pages)
        outcome = _formula_outcomes(doc)[0]
        self.assertTrue(any(i.startswith("cross_page_parameter_relation_uncertain")
                            for i in outcome["issues"]))
        self.assertEqual(outcome["quality_status"], "needs_review")

    def test_no_adjacency_invention_without_relations(self):
        d1 = _pi_formula_region("up-f1")
        _write_cache(self.cache_dir, _DUMMY_SHA, {1: [_cache_region()]})
        pages, _ = _load(self.cache_dir)
        doc = _assemble(_page_doc([_page([d1])]), pages)
        outcome = _formula_outcomes(doc)[0]
        cross = [i for i in outcome["issues"]
                 if i.startswith("cross_page_parameter_relation")]
        self.assertEqual(cross, [])


# --------------------------------------------------------------------------- #
# 4. Input-order stability
# --------------------------------------------------------------------------- #

class TestOrderStability(unittest.TestCase):
    def test_shuffled_inputs_produce_identical_ids(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        d3 = _pi_formula_region("up-f3", bbox=[100.0, 400.0, 400.0, 460.0])
        c1 = _cache_region(candidate_id="layout-x-0")
        c2 = _cache_region(bbox=[100.0, 300.0, 400.0, 360.0], latex="L = 2.0 h",
                           candidate_id="layout-x-1")
        c3 = _cache_region(bbox=[100.0, 400.0, 400.0, 460.0], latex="V = A h",
                           score=0.8, candidate_id="layout-x-2")
        tmp = tempfile.mkdtemp(prefix="t04-order-")
        try:
            cache_dir = os.path.join(tmp, _RUN_ID)
            _write_cache(cache_dir, _DUMMY_SHA, {1: [c1, c2, c3]})
            pages, _ = _load(cache_dir)
            base = _assemble(_page_doc([_page([d1, d2, d3])]), pages)

            cache_dir2 = os.path.join(tmp, "rev", _RUN_ID)
            os.makedirs(cache_dir2, exist_ok=True)
            for page, regions in {1: [c3, c1, c2]}.items():
                payload = _cache_file(_DUMMY_SHA, page, regions)
                with open(os.path.join(cache_dir2,
                                       f"{_DUMMY_SHA}-{page}.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(payload, fh)
            pages_rev, _ = load_formula_pages(
                _DUMMY_SHA, range(1, 2), cache_dir=cache_dir2)
            shuffled = _assemble(_page_doc([_page([d3, d1, d2])]), pages_rev)
            self.assertEqual(
                compute_ids(base)["canonical_id"],
                compute_ids(shuffled)["canonical_id"])
            self.assertEqual(
                compute_ids(base)["canonical_content_id"],
                compute_ids(shuffled)["canonical_content_id"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 5. Review/assertion upgrade boundary + validator reconciliation
# --------------------------------------------------------------------------- #

class TestReviewUpgradeBoundary(unittest.TestCase):
    def _valid_doc_with_formula(self):
        """A validated assembled doc to mutate structurally."""
        page = _page([_pi_formula_region("up-f1")])
        page_doc = _page_doc([page])
        return assemble_document(page_doc, formula_pages={
            1: [{"kind": "formula", "score": 0.9, "bbox": list(_BBOX),
                 "raw_latex": "T = \\sum p_j", "physical_page": 1}]})

    def test_formula_outcome_passed_without_review_id_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"][0]["quality_status"] = "passed"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("review_assertion", str(ctx.exception))

    def test_formula_outcome_passed_with_review_id_accepted(self):
        doc = self._valid_doc_with_formula()
        element = doc["elements"][0]
        outcome = doc["structured_outcomes"][0]
        # bind a review id at BOTH the outcome and its element
        outcome["quality_status"] = "passed"
        outcome["issues"].append("review_assertion:rev-2026-001")
        element["quality_status"] = "passed"
        element["policy_reasons"] = ["review_assertion:rev-2026-001"]
        model = CanonicalDocument.from_dict(doc)  # must not raise
        self.assertIsNotNone(model)

    def test_formula_element_passed_without_review_id_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["elements"][0]["quality_status"] = "passed"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("review_assertion", str(ctx.exception))

    def test_illegal_formula_execution_status_needs_review_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"][0]["execution_status"] = "needs_review"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("illegal execution_status", str(ctx.exception))

    def test_table_route_on_formula_attempt_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"][0]["attempts"][0]["route"] = "cropped_table"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("illegal route", str(ctx.exception))

    def test_quality_mismatch_between_outcome_and_element_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["elements"][0]["quality_status"] = "needs_review"
        doc["structured_outcomes"][0]["quality_status"] = "quarantined"
        doc["structured_outcomes"][0]["issues"].append("review_assertion:x")
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("does not match element", str(ctx.exception))

    def test_marker_gate_formula_elements_without_outcomes_rejected(self):
        """Bypass attempt: strip the formula outcomes from a marked document.
        The marker declares T04 reconciliation, so formula elements without a
        formula-outcome ledger fail loudly (never silently 'legacy')."""
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"] = [
            o for o in doc["structured_outcomes"] if o.get("kind") != "formula"]
        doc["formula_ledger"]["outcome_count"] = 0
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn(
            "formula elements present without a formula-outcome ledger",
            str(ctx.exception))

    def test_outcome_pointing_at_unknown_element_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"][0]["element_id"] = "e-nonexistent"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("unknown element", str(ctx.exception))

    def test_marker_outcome_count_mismatch_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["formula_ledger"]["outcome_count"] = 5
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("outcome_count", str(ctx.exception))

    def test_legacy_artifact_without_marker_still_validates(self):
        """Published pre-T04 artifacts: formula elements + audit fields + table
        outcomes, no formula outcomes, no marker -> must keep validating."""
        doc = self._valid_doc_with_formula()
        doc["structured_outcomes"] = []
        doc.pop("formula_ledger")
        model = CanonicalDocument.from_dict(doc)  # must not raise
        self.assertIsNotNone(model)

    def test_bad_marker_version_rejected(self):
        doc = self._valid_doc_with_formula()
        doc["formula_ledger"]["version"] = "t99-1"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("formula_ledger.version", str(ctx.exception))


# --------------------------------------------------------------------------- #
# 6. Identity flips (requirement 7)
# --------------------------------------------------------------------------- #

class TestIdentityFlips(unittest.TestCase):
    def _assembled(self, *, latex="T = \\sum p_j", relations=None, score=0.9):
        page = _page([_pi_formula_region("up-f1", relations=relations)])
        page_doc = _page_doc([page])
        pages, _ = _load_cache_dir(latex=latex, score=score)
        return assemble_document(page_doc, formula_pages=pages)

    def test_latex_change_flips_content_and_canonical_ids(self):
        base = self._assembled()
        changed = self._assembled(latex="T = \\sum q_j")
        ids_base = compute_ids(base)
        ids_changed = compute_ids(changed)
        self.assertNotEqual(ids_base["canonical_content_id"],
                            ids_changed["canonical_content_id"])
        self.assertNotEqual(ids_base["canonical_id"], ids_changed["canonical_id"])

    def test_outcome_only_change_flips_canonical_but_not_content(self):
        base = self._assembled()
        changed = self._assembled(relations=[{
            "kind": "explicit_numbered_formula_parameter_reference",
            "formula_number": "5.4.2-2", "source": {"physical_page": 3},
            "verified": False}])
        ids_base = compute_ids(base)
        ids_changed = compute_ids(changed)
        self.assertNotEqual(ids_base["canonical_id"], ids_changed["canonical_id"])
        self.assertEqual(ids_base["canonical_content_id"],
                         ids_changed["canonical_content_id"])


def _load_cache_dir(*, latex, score):
    tmp = tempfile.mkdtemp(prefix="t04-ident-")
    cache_dir = os.path.join(tmp, _RUN_ID)
    _write_cache(cache_dir, _DUMMY_SHA, {1: [_cache_region(latex=latex, score=score)]})
    return load_formula_pages(_DUMMY_SHA, range(1, 2), cache_dir=cache_dir)


# --------------------------------------------------------------------------- #
# 6b. T04-R2: lossless source provenance (review round 2)
# --------------------------------------------------------------------------- #

_FULL_RELATION = {
    "kind": "uncertain_next_page_parameter_marker",
    "source": {
        "element_id": "p0037-b0001",
        "physical_page": 2,
        "bbox": [113.88, 71.90, 356.18, 90.35],
        "text": "式中:l为(见图6-3)",
    },
    "verified": False,
    "association_status": "uncertain",
    "basis": "bare_original_marker_on_following_page_has_no_formula_identifier",
}


class TestLosslessProvenance(unittest.TestCase):
    """T04-R2: elements must trace back to the ORIGINAL detected regions."""

    def _assemble_one(self, pi_overrides: dict | None = None, cache_files=None):
        d1 = _pi_formula_region("up-f1")
        if pi_overrides:
            d1.update(pi_overrides)
        page = _page([d1])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        return _assemble(_page_doc([page]), pages)

    def test_element_traces_back_to_original_region(self):
        d1 = _pi_formula_region("up-f1", issues=["units_not_verified"])
        page = _page([d1])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = _assemble(_page_doc([page]), pages)
        el = _formula_elements(doc)[0]
        outcome = _formula_outcomes(doc)[0]
        # verbatim source region id on BOTH the outcome and the element
        self.assertEqual(outcome["region_id"], "up-f1")
        self.assertEqual(el["source_region_ids"], ["up-f1"])
        # detection-side and recognition-side processing configs, both real
        self.assertIn(d1["processing_config_id"], el["processing_config_ids"])
        self.assertIn(_PROFILE, el["processing_config_ids"])
        # upstream quality issues preserved verbatim
        self.assertIn("units_not_verified", el["quality_issues"])
        self.assertEqual(el["execution_status"], "completed")

    def test_missing_region_id_derives_fallback(self):
        d1 = _pi_formula_region("up-f1")
        del d1["region_id"]  # historical record without a region id
        page = _page([d1])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = _assemble(_page_doc([page]), pages)
        outcome = _formula_outcomes(doc)[0]
        el = _formula_elements(doc)[0]
        self.assertTrue(outcome["region_id"].startswith("fx-"))
        self.assertEqual(el["source_region_ids"], [outcome["region_id"]])
        # the recognition result identity stays fx-derived as well
        self.assertTrue(outcome["selected_result_id"].startswith("fx-"))

    def test_duplicate_source_region_id_hard_fails(self):
        d1 = _pi_formula_region("up-dup")
        d2 = _pi_formula_region("up-dup", bbox=[100.0, 300.0, 400.0, 360.0])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        with self.assertRaises(ValueError) as ctx:
            _assemble(_page_doc([_page([d1, d2])]), pages)
        self.assertIn("duplicate/conflicting formula source region_id",
                      str(ctx.exception))

    def test_engine_registered_from_cache_and_referenced(self):
        doc = self._assemble_one()
        engines = {e["engine_id"]: e for e in doc["generation"]["engines"]}
        self.assertIn("formula-1", engines)
        self.assertEqual(engines["formula-1"]["name"], "PP-FormulaNet_plus-M")
        self.assertEqual(engines["formula-1"]["version"], "3.7.0")
        el = _formula_elements(doc)[0]
        self.assertEqual(el["provenance"][0]["engine_id"], "formula-1")
        self.assertNotIn("PP-FormulaNet", json.dumps(
            [e for e in doc["generation"]["engines"] if e["engine_id"] != "formula-1"]))

    def test_no_formula_engine_registered_without_cache(self):
        # legacy path (formula_pages=None): no formula engine invented
        page = _page([_pi_formula_region("up-f1")])
        doc = assemble_document(_page_doc([page]), formula_pages=None)
        self.assertNotIn(
            "formula-1", {e["engine_id"] for e in doc["generation"]["engines"]})

    def test_engine_version_change_flips_canonical_not_content(self):
        page = _page([_pi_formula_region("up-f1")])
        page_doc = _page_doc([page])
        pages_a, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        # the same recognition under a DIFFERENT engine version in the cache
        tmp = tempfile.mkdtemp(prefix="t04-engv-")
        cache_dir = os.path.join(tmp, _RUN_ID)
        payload = _cache_file(_DUMMY_SHA, 1, [_cache_region(latex="T = \\sum p_j",
                                                            score=0.9)])
        payload["result"]["package_versions"]["paddleocr"] = "3.8.1"
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f"{_DUMMY_SHA}-1.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(payload, fh)
        pages_b, _ = load_formula_pages(_DUMMY_SHA, range(1, 2), cache_dir=cache_dir)
        base = assemble_document(page_doc, formula_pages=pages_a)
        changed = assemble_document(page_doc, formula_pages=pages_b)
        ids_a, ids_b = compute_ids(base), compute_ids(changed)
        self.assertNotEqual(ids_a["canonical_id"], ids_b["canonical_id"])
        self.assertEqual(ids_a["canonical_content_id"], ids_b["canonical_content_id"])
        self.assertNotEqual(
            [e for e in base["generation"]["engines"] if e["engine_id"] == "formula-1"],
            [e for e in changed["generation"]["engines"] if e["engine_id"] == "formula-1"])

    def test_full_relation_round_trip(self):
        d1 = _pi_formula_region("up-f1", relations=[dict(_FULL_RELATION)])
        # the relation source lives on page 2 -- a real two-page document so
        # the projected source span has a parsed page to live on
        page_doc = _page_doc([_page([d1]), _page([], pno=2)])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = _assemble(page_doc, pages)
        el = _formula_elements(doc)[0]
        rels = el["cross_page_relations"]
        self.assertEqual(len(rels), 1)
        rec = rels[0]
        self.assertTrue(rec["relation_id"].startswith("frel-"))
        self.assertEqual(rec["kind"], _FULL_RELATION["kind"])
        self.assertEqual(rec["basis"], _FULL_RELATION["basis"])
        self.assertIs(rec["verified"], False)
        self.assertEqual(rec["association_status"], "uncertain")
        src = rec["source"]
        self.assertEqual(src["element_id"], "p0037-b0001")
        self.assertEqual(src["physical_page"], 2)
        self.assertEqual(src["bbox"], _FULL_RELATION["source"]["bbox"])
        self.assertEqual(src["text"], _FULL_RELATION["source"]["text"])
        # projected as an auditable source span on the parsed page
        self.assertIsNotNone(rec["source_span_id"])
        span_ids = {s["span_id"] for s in el["source_spans"]}
        self.assertIn(rec["source_span_id"], span_ids)
        span = [s for s in el["source_spans"]
                if s["span_id"] == rec["source_span_id"]][0]
        self.assertEqual(span["physical_page"], 2)
        self.assertEqual(span["bbox"], _FULL_RELATION["source"]["bbox"])
        # schema round-trip preserves the record losslessly
        model = CanonicalDocument.from_dict(doc)
        again = model.to_dict()
        el2 = [e for e in again["elements"] if e["type"] == "formula"][0]
        self.assertEqual(el2["cross_page_relations"], rels)
        # unconfirmed relation keeps the element at needs_review (D1)
        self.assertEqual(el2["quality_status"], "needs_review")

    def test_resolved_target_becomes_related_link(self):
        d1 = _pi_formula_region("up-f1")
        d2 = _pi_formula_region("up-f2", bbox=[100.0, 300.0, 400.0, 360.0])
        rel = dict(_FULL_RELATION)
        rel["target"] = {"region_id": "up-f2", "physical_page": 2}
        d1["relations"] = [rel]  # up-f1 carries a relation targeting up-f2
        pages, _ = _load_cache_two(d2)
        doc = _assemble(_page_doc([_page([d1, d2])]), pages)
        els = {e["source_region_ids"][0]: e for e in _formula_elements(doc)}
        target = els["up-f2"]
        source = els["up-f1"]
        rec = source["cross_page_relations"][0]
        self.assertEqual(rec["target_element_id"], target["element_id"])
        self.assertIn({"type": "related",
                       "target_element_id": target["element_id"]},
                      source["links"])

    def test_unresolved_target_explicit_issue_no_dangling_link(self):
        d1 = _pi_formula_region("up-f1")
        rel = dict(_FULL_RELATION)
        rel["target"] = {"region_id": "up-nonexistent", "physical_page": 2}
        d1["relations"] = [rel]
        page = _page([d1])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = _assemble(_page_doc([page]), pages)
        el = _formula_elements(doc)[0]
        outcome = _formula_outcomes(doc)[0]
        issue = "cross_page_relation_unresolved_target:up-nonexistent"
        self.assertIn(issue, el["quality_issues"])
        self.assertIn(issue, outcome["issues"])
        self.assertEqual(el["links"], [])
        self.assertIsNone(el["cross_page_relations"][0]["target_element_id"])
        # needs_review is never promoted by an unconfirmed relation
        self.assertEqual(el["quality_status"], "needs_review")

    def test_relation_change_flips_canonical_not_content(self):
        page = _page([_pi_formula_region("up-f1", relations=[dict(_FULL_RELATION)])])
        page_doc = _page_doc([page])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        base = assemble_document(page_doc, formula_pages=pages)
        changed_rel = dict(_FULL_RELATION)
        changed_rel["source"] = dict(_FULL_RELATION["source"], text="式中:l为修正后")
        page2 = _page([_pi_formula_region("up-f1", relations=[changed_rel])])
        changed = assemble_document(_page_doc([page2]), formula_pages=pages)
        ids_a, ids_b = compute_ids(base), compute_ids(changed)
        self.assertNotEqual(ids_a["canonical_id"], ids_b["canonical_id"])
        self.assertEqual(ids_a["canonical_content_id"], ids_b["canonical_content_id"])

    def test_source_region_id_round_trip(self):
        doc = self._assemble_one()
        model = CanonicalDocument.from_dict(doc)
        again = model.to_dict()
        el_in = _formula_elements(doc)[0]
        el_out = [e for e in again["elements"] if e["type"] == "formula"][0]
        self.assertEqual(el_out["source_region_ids"], el_in["source_region_ids"])
        self.assertEqual(el_out["crop_assets"], el_in["crop_assets"])
        self.assertEqual(el_out["execution_status"], "completed")
        # element -> original detected region traceability, provenance intact
        self.assertEqual(el_out["provenance"][0]["engine_id"],
                         el_in["provenance"][0]["engine_id"])


# --------------------------------------------------------------------------- #
# 6c. T04-R3: outcome-level lossless provenance (every execution status)
# --------------------------------------------------------------------------- #

_ASSET_REF = "data/model_runtime/unified_pdf/x/1/up-f1.png"
_ASSET_SHA = "b" * 64


class TestOutcomeProvenanceClosure(unittest.TestCase):
    """T04-R3: completed, unsupported AND failed outcomes all preserve the
    obtainable crop asset + structured relations + both configs, and the
    completed pair (outcome, element) must agree or validation rejects."""

    def _assemble_status(self, *, pi: dict | None = None, cache_files=None,
                         cache_pages=None):
        d1 = _pi_formula_region("up-f1", issues=["units_not_verified"])
        d1["image_ref"] = _ASSET_REF
        d1["image_sha256"] = _ASSET_SHA
        rel = {
            "kind": "uncertain_next_page_parameter_marker",
            "source": {"element_id": "p0037-b0001", "physical_page": 2,
                       "bbox": [113.88, 71.90, 356.18, 90.35],
                       "text": "式中:l为(见图6-3)"},
            "verified": False, "association_status": "uncertain",
            "basis": "bare_original_marker_on_following_page",
        }
        d1["relations"] = [rel]
        if pi:
            d1.update(pi)
        # Hosted by a text block holding it, so a low-score result closes as unsupported.
        page = _page([d1, _host_text_region(_BBOX)])
        pages = cache_pages if cache_pages is not None else {1: []}
        return _assemble(_page_doc([page]), pages)

    def test_low_score_unsupported_retains_asset_and_relations(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.3)
        doc = self._assemble_status(cache_pages=pages)
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "unsupported")
        # asset preserved verbatim -- usable even without any element
        self.assertEqual(outcome["crop_assets"], [{"ref": _ASSET_REF,
                                                   "sha256": _ASSET_SHA}])
        rels = outcome["cross_page_relations"]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["kind"],
                         "uncertain_next_page_parameter_marker")
        self.assertEqual(rels[0]["source"]["text"], "式中:l为(见图6-3)")
        self.assertEqual(rels[0]["source"]["physical_page"], 2)
        self.assertEqual(rels[0]["basis"],
                         "bare_original_marker_on_following_page")
        self.assertIs(rels[0]["verified"], False)
        # both configs, verbatim
        self.assertEqual(outcome["detection_config_id"],
                         "unified-local-v5:cfg")
        self.assertEqual(outcome["recognition_config_id"], _PROFILE)
        attempt = outcome["attempts"][0]
        self.assertEqual(attempt["engine_name"], "PP-FormulaNet_plus-M")
        self.assertEqual(attempt["input_identity"], f"asset-sha256:{_ASSET_SHA}")
        self.assertNotIn("missing_crop_asset", outcome["issues"])

    def test_empty_latex_failed_retains_asset_and_relations(self):
        pages, _ = _load_cache_dir(latex="", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "failed")
        self.assertIn("empty_formula_recognition", outcome["issues"])
        self.assertEqual(outcome["crop_assets"], [{"ref": _ASSET_REF,
                                                   "sha256": _ASSET_SHA}])
        self.assertEqual(len(outcome["cross_page_relations"]), 1)
        self.assertEqual(outcome["recognition_config_id"], _PROFILE)
        self.assertEqual(outcome["detection_config_id"], "unified-local-v5:cfg")

    def test_cache_missing_failed_retains_asset_and_relations(self):
        doc = self._assemble_status(cache_pages={1: []})
        outcome = _formula_outcomes(doc)[0]
        self.assertEqual(outcome["execution_status"], "failed")
        self.assertIn("formula_cache_result_missing", outcome["issues"])
        # no recognition happened, but everything OBTAINABLE from the
        # detection side is preserved: asset, relations, detection config
        self.assertEqual(outcome["crop_assets"], [{"ref": _ASSET_REF,
                                                   "sha256": _ASSET_SHA}])
        self.assertEqual(len(outcome["cross_page_relations"]), 1)
        self.assertEqual(outcome["detection_config_id"], "unified-local-v5:cfg")
        self.assertIsNone(outcome["recognition_config_id"])
        # self-sufficient: region identity + page + bbox locate the source
        self.assertEqual(outcome["region_id"], "up-f1")
        self.assertEqual(outcome["physical_page"], 1)

    def test_completed_outcome_provenance_matches_element(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        outcome = _formula_outcomes(doc)[0]
        el = _formula_elements(doc)[0]
        self.assertEqual(outcome["execution_status"], "completed")
        self.assertEqual(outcome["crop_assets"], el["crop_assets"])
        self.assertEqual(outcome["cross_page_relations"],
                         el["cross_page_relations"])
        self.assertIn(outcome["detection_config_id"],
                      el["processing_config_ids"])
        self.assertIn(outcome["recognition_config_id"],
                      el["processing_config_ids"])
        self.assertIn(outcome["region_id"], el["source_region_ids"])

    def test_completed_outcome_crop_asset_mismatch_rejected(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        doc["structured_outcomes"][0]["crop_assets"] = []
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("crop_assets do not match", str(ctx.exception))

    def test_completed_outcome_relation_mismatch_rejected(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        doc["structured_outcomes"][0]["cross_page_relations"] = []
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("cross_page_relations do not match", str(ctx.exception))

    def test_completed_outcome_source_id_mismatch_rejected(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        doc["elements"] = [e for e in doc["elements"]
                           if e["type"] != "formula"] + [
            e for e in doc["elements"] if e["type"] == "formula"]
        el = [e for e in doc["elements"] if e["type"] == "formula"][0]
        el["source_region_ids"] = ["up-tampered"]
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("does not trace", str(ctx.exception))

    def test_completed_outcome_config_mismatch_rejected(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        doc["structured_outcomes"][0]["detection_config_id"] = "cfg-forged"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("detection_config_id", str(ctx.exception))

    def test_selected_attempt_config_mismatch_rejected(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = self._assemble_status(cache_pages=pages)
        outcome = doc["structured_outcomes"][0]
        # the detection config IS among the element's processing configs, so
        # only the attempt/config disagreement can fire here
        outcome["recognition_config_id"] = outcome["detection_config_id"]
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("does not match recognition_config_id",
                      str(ctx.exception))

    def test_marker_requires_provenance_keys_on_every_formula_outcome(self):
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.3)
        doc = self._assemble_status(cache_pages=pages)
        outcome = doc["structured_outcomes"][0]
        for key in ("crop_assets", "cross_page_relations",
                    "detection_config_id", "recognition_config_id"):
            outcome.pop(key)
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("lossless-provenance keys", str(ctx.exception))


# --------------------------------------------------------------------------- #
# 7. Chunk policy / audit identity (chain end)
# --------------------------------------------------------------------------- #

class TestChunkPolicy(unittest.TestCase):
    def test_formula_chunk_is_locator_only(self):
        from src.application.v2_chunker import chunk_document

        page = _page([_pi_formula_region("up-f1")])
        page_doc = _page_doc([page])
        pages, _ = _load_cache_dir(latex="T = \\sum p_j", score=0.9)
        doc = _assemble(page_doc, pages)
        chunks = chunk_document(doc, token_budget=480, overlap=0,
                                tokenizer=_CharTokenizer())
        formula_chunks = [c for c in chunks
                          if "formula" in c.metadata.get("element_types", [])]
        self.assertTrue(formula_chunks, "expected a formula chunk")
        for c in formula_chunks:
            self.assertEqual(c.metadata["usage_policy"], "source_locator_only")
            self.assertEqual(c.metadata["quality_status"], "needs_review")


class _CharTokenizer:
    def count(self, text: str) -> int:
        return len(text)

    def encode(self, text: str, **_kwargs):  # noqa: D401
        return list(text)


# --------------------------------------------------------------------------- #
# 8. Real-corpus read-only reconciliation (skips when data absent)
# --------------------------------------------------------------------------- #

class TestRealCorpusReconciliation(unittest.TestCase):
    """Read-only: assemble every corpus document in memory and assert the
    three-way identity reconciliation holds (detected == outcome counts,
    completed == elements). Writes nothing."""

    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _PI = os.path.join(_REPO, "data", "canonical",
                       "corpus-37456321a968-documents.jsonl")
    _CACHE = os.path.join(_REPO, "data", "model_runtime", "corpus_formulas",
                          "5d3264515515e228")
    _RUN_ID = "5d3264515515e228"

    def test_corpus_reconciles(self):
        if not os.path.isfile(self._PI):
            self.skipTest("real corpus page-intermediate not present")
        from collections import Counter
        totals: Counter = Counter()
        with open(self._PI, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                page_doc = json.loads(line)
                sha = page_doc["sha256"]
                n = len(page_doc.get("pages") or [])
                detected = sum(
                    1 for p in page_doc.get("pages") or []
                    for r in p.get("regions") or []
                    if r.get("kind") == "formula")
                pages, _failures = load_formula_pages(
                    sha, range(1, n + 1), cache_dir=self._CACHE)
                doc = assemble_document(page_doc, formula_pages=pages)
                from src.application.canonical_validation import (
                    compute_ids as _compute_ids,
                )
                doc.update(_compute_ids(doc))
                outcomes = _formula_outcomes(doc)
                completed = sum(1 for o in outcomes
                                if o["execution_status"] == "completed")
                # three-way identity: detected == outcomes == assembled side
                self.assertEqual(
                    len(outcomes), detected,
                    f"{sha[:12]}: one outcome per detected formula region")
                self.assertEqual(
                    completed, len(_formula_elements(doc)),
                    f"{sha[:12]}: completed outcomes == formula elements")
                validate_document(doc)
                totals["docs"] += 1
                totals["detected"] += detected
                totals["outcomes"] += len(outcomes)
                for o in outcomes:
                    totals[f"exec_{o['execution_status']}"] += 1
                totals["elements"] += len(_formula_elements(doc))
        self.assertEqual(totals["docs"], 21)
        self.assertEqual(totals["detected"], totals["outcomes"])
        print("\n[real-corpus formula reconciliation]", dict(totals))


if __name__ == "__main__":
    unittest.main()
