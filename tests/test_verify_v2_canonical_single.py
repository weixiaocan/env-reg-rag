"""Unit tests for the CECS758 V2 single-SHA verification script (plan 1.6).

Run::

    PYTHONPATH=src .venv/Scripts/python.exe -m unittest \\
        tests.test_verify_v2_canonical_single -v
"""
from __future__ import annotations

import json
import os
import unittest

# Ensure the script is importable when run via ``python -m unittest``.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys_path_added = False
if _REPO_ROOT not in __import__("sys").path:
    __import__("sys").path.insert(0, _REPO_ROOT)
    sys_path_added = True

import scripts.verify_v2_canonical_single as vmod  # noqa: E402
from src.application.canonical_assembly import CECS758_SHA  # noqa: E402

_CASES_JSONL = os.path.join(_REPO_ROOT, "data", "review", "v2", "pdf_pipeline_cases.jsonl")
_VERIFICATION_JSON = os.path.join(
    _REPO_ROOT,
    "data",
    "canonical",
    "v2",
    vmod.DEFAULT_CORPUS_VERSION,
    f"{CECS758_SHA}.verification.json",
)


def _count_cases(sha: str) -> int:
    n = 0
    with open(_CASES_JSONL, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if json.loads(line).get("pdf_sha256") == sha:
                n += 1
    return n


class TestVerifyReport(unittest.TestCase):
    """End-to-end + structural assertions on the verification report."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = vmod.verify_document(CECS758_SHA, vmod.DEFAULT_CORPUS_VERSION)

    # -- 1. end-to-end --------------------------------------------------------
    def test_validate_document_passes(self):
        self.assertEqual(self.report["validate_document"], "pass")
        self.assertIsNone(self.report["validation_error"])

    def test_exit_code_zero(self):
        self.assertEqual(self.report["exit_code"], 0)

    def test_regression_all_pass(self):
        reg = self.report["regression"]
        self.assertEqual(reg["total"], 3, "CECS758 has exactly 3 regression cases")
        self.assertTrue(reg["all_pass"])
        self.assertEqual(reg["pass"], 3)
        self.assertEqual(reg["pass_rate"], 1.0)

    def test_regression_case_level_status(self):
        for cr in self.report["cases"]:
            if cr["regression"]:
                self.assertIn(
                    cr["case_status"], ("pass", "partial"),
                    msg=f"regression case {cr['case_id']} must not fail",
                )
                self.assertNotEqual(cr["case_status"], "fail")

    def test_non_regression_meets_v1_baseline(self):
        nr = self.report["non_regression"]
        self.assertTrue(nr["meets_baseline"])
        self.assertGreaterEqual(
            nr["v2_baseline_pass_rate"], nr["v1_baseline_pass_rate"]
        )

    # -- 2. report structure --------------------------------------------------
    def test_report_covers_all_eight_cases(self):
        expected = _count_cases(CECS758_SHA)
        self.assertEqual(self.report["case_count"], expected)
        self.assertEqual(len(self.report["cases"]), expected)
        pages = {cr["physical_page"] for cr in self.report["cases"]}
        self.assertEqual(pages, {10, 24, 28, 35, 44, 63, 67, 68})

    def test_every_assertion_has_status(self):
        for cr in self.report["cases"]:
            for a in cr["assertions"]:
                self.assertIn(a["status"], ("pass", "partial", "fail"))
                self.assertIn("matched_fragments", a)
                self.assertIn("notes", a)
                self.assertIsNotNone(a["assertion_id"])

    def test_case_status_field_present(self):
        for cr in self.report["cases"]:
            self.assertIn(cr["case_status"], ("pass", "partial", "fail"))

    # -- 3. regression detail -------------------------------------------------
    def _case(self, page: int) -> dict:
        for cr in self.report["cases"]:
            if cr["physical_page"] == page:
                return cr
        raise AssertionError(f"case for page {page} missing")

    def test_p24_formula_matched_latex_and_section_path(self):
        cr = self._case(24)
        self.assertTrue(cr["regression"])
        self.assertEqual(cr["case_status"], "pass")
        a = cr["assertions"][0]
        self.assertEqual(a["assertion_id"], "cecs758-flow-5.4.2-2")
        frags = " ".join(a["matched_fragments"])
        self.assertIn("latex", frags)
        self.assertIn("section_path", frags)
        # Structural detail carries the actual latex + bbox.
        sd = cr["structural_detail"]
        self.assertTrue(sd["formulas"])
        f = sd["formulas"][0]
        self.assertIn("Q=", f["latex"])
        self.assertIn("\\frac", f["latex"])
        self.assertIn("\\sum", f["latex"])
        self.assertTrue(f["primary_bbox"])

    def test_p67_continuation_has_continues_link(self):
        cr = self._case(67)
        self.assertTrue(cr["regression"])
        self.assertEqual(cr["case_status"], "pass")
        # Find the table_cells assertion.
        cont = next(
            a for a in cr["assertions"]
            if a["assertion_type"] == "table_cells"
        )
        frags = " ".join(cont["matched_fragments"])
        self.assertIn("continues link", frags)
        # Structural detail: the 续表 table carries a continues link.
        sd = cr["structural_detail"]
        tables = sd["tables"]
        has_continues = any(
            any(lnk.get("type") == "continues" for lnk in (t.get("links") or []))
            for t in tables
        )
        self.assertTrue(has_continues, "p67 续表 must carry a continues link")
        # Clean parameter names recoverable.
        cells_blob = " ".join(
            c.get("text", "") for t in tables for c in (t.get("cells") or [])
        )
        for name in ("指标参数", "参照值", "总氮", "磷酸盐", "氯化物", "氟化物"):
            self.assertIn(name, cells_blob)

    def test_p68_has_figure_caption(self):
        cr = self._case(68)
        self.assertTrue(cr["regression"])
        self.assertEqual(cr["case_status"], "pass")
        sd = cr["structural_detail"]
        self.assertTrue(sd["figures"], "p68 must have a figure element")
        fig = sd["figures"][0]
        self.assertIsNotNone(fig["caption"])
        self.assertIn("图3", fig["caption"])
        self.assertIsNotNone(fig["asset"])
        self.assertGreater(fig["asset"]["pixel_width"], 0)
        # p68 must also have the circular formula.
        self.assertTrue(sd["formulas"])
        latexes = " ".join(f["latex"] for f in sd["formulas"])
        self.assertIn("\\frac", latexes)

    # -- 4. baseline comparison ----------------------------------------------
    def test_v2_strict_pass_rate_meets_v1(self):
        nr = self.report["non_regression"]
        self.assertGreaterEqual(
            nr["v2_strict_pass_rate"], nr["v1_baseline_strict_pass_rate"] - 1e-9
        )

    def test_no_structural_fail(self):
        """No assertion may be a structural fail (V2 produced wrong structure)."""
        for cr in self.report["cases"]:
            for a in cr["assertions"]:
                self.assertNotEqual(
                    a["status"], "fail",
                    msg=f"{cr['case_id']}/{a['assertion_id']} is a structural fail",
                )

    # -- 5. artifact on disk --------------------------------------------------
    def test_verification_json_written(self):
        self.assertTrue(os.path.isfile(_VERIFICATION_JSON))
        with open(_VERIFICATION_JSON, "r", encoding="utf-8") as fh:
            disk = json.load(fh)
        self.assertEqual(disk["sha"], CECS758_SHA)
        self.assertEqual(disk["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
