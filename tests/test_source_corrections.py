"""Owner-reviewed corrections replace recognized content at assembly time."""

import hashlib
import json
import unittest

from src.application.source_corrections import (
    SourceCorrectionError,
    apply_source_corrections,
    load_source_corrections,
)
from tests import test_t07_recovery_text_override as t07

SHA = "a" * 64
WRONG = r"Q_{\mathrm{rl}}=\frac{\sum RDII_{j}}{\sum R_{j}}"
RIGHT = r"Q_{\mathrm{rl}}=\frac{\frac{\sum RDII_{j}}{\sum R_{j}}}{\sum L_{i}}"
VARS_WRONG = "式中：Q——污水收集系统的降雨入流入渗评估值"
VARS_RIGHT = "式中：Q_rl——污水收集系统的降雨入流入渗评估值"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span(page, bbox):
    return [{"span_id": "s1", "role": "primary", "physical_page": page, "bbox": bbox}]


def _document():
    return {
        "source": {"sha256": SHA},
        "elements": [
            {"element_id": "e1", "type": "formula", "quality_status": "needs_review",
             "policy_reasons": ["structured_default_locator_only"], "quality_issues": [],
             "content": {"latex": WRONG, "context_text": VARS_WRONG},
             "source_spans": _span(22, [242.9, 91.8, 364.0, 215.3])},
            {"element_id": "e2", "type": "text", "quality_status": "needs_review",
             "quality_issues": [], "content": {"text": VARS_WRONG},
             "source_spans": _span(22, [80.5, 218.4, 521.4, 240.5])},
            # same transcription elsewhere on the page: the bbox picks the right one
            {"element_id": "e3", "type": "formula", "quality_status": "needs_review",
             "quality_issues": [], "content": {"latex": WRONG, "context_text": None},
             "source_spans": _span(22, [240.0, 400.0, 360.0, 430.0])},
        ],
        "structured_outcomes": [
            {"kind": "formula", "element_id": "e1", "quality_status": "needs_review"},
            {"kind": "formula", "element_id": "e3", "quality_status": "needs_review"},
        ],
    }


def _registry(**overrides):
    registry = {
        "registry_sha256": "f" * 64,
        "formulas": [{"source_sha256": SHA, "physical_page": 22,
                      "bbox": [242.9385, 91.7975, 363.9741, 215.3262],
                      "original_latex_sha256": _sha(WRONG), "corrected_latex": RIGHT}],
        "texts": [{"source_sha256": SHA, "physical_page": 22,
                   "original_text_sha256": _sha(VARS_WRONG), "corrected_text": VARS_RIGHT}],
    }
    registry.update(overrides)
    return registry


class ApplySourceCorrectionsTest(unittest.TestCase):
    def test_formula_and_its_variable_line_are_replaced(self):
        doc = _document()

        applied = apply_source_corrections(doc, _registry())

        formula, text, other = doc["elements"]
        self.assertEqual(applied, 2)
        self.assertEqual(formula["content"]["latex"], RIGHT)
        self.assertEqual(formula["content"]["context_text"], VARS_RIGHT)
        self.assertEqual(text["content"]["text"], VARS_RIGHT)
        self.assertEqual(other["content"]["latex"], WRONG)

    def test_correction_keeps_trust_and_records_its_registry(self):
        doc = _document()

        apply_source_corrections(doc, _registry())

        formula = doc["elements"][0]
        self.assertEqual(formula["quality_status"], "needs_review")
        self.assertEqual(formula["policy_reasons"], ["structured_default_locator_only"])
        self.assertEqual(formula["quality_issues"], ["source_correction:ffffffffffff:owner_reviewed"])

    def test_owner_verified_corrections_pass_review_with_a_traceable_binding(self):
        from src.application.evidence_policy import element_usage_policy

        doc = _document()

        apply_source_corrections(doc, _registry(owner_verified=True))

        formula, text, other = doc["elements"]
        binding = "review_assertion:source-corrections:ffffffffffff"
        self.assertEqual(formula["quality_status"], "passed")
        self.assertIn(binding, formula["policy_reasons"])
        self.assertEqual(element_usage_policy(formula), "answer_and_citation")
        self.assertEqual(text["quality_status"], "passed")
        self.assertEqual(doc["structured_outcomes"][0]["quality_status"], "passed")
        self.assertIn(binding, doc["structured_outcomes"][0]["issues"])
        self.assertEqual(doc["structured_outcomes"][1]["quality_status"], "needs_review")
        self.assertEqual(other["quality_status"], "needs_review")
        self.assertEqual(element_usage_policy(other), "source_locator_only")

    def test_agent_checked_entry_passes_and_says_who_checked_it(self):
        doc = _document()
        registry = _registry(owner_verified=True, texts=[])
        registry["formulas"] = [{**registry["formulas"][0], "verified_by": "agent"}]

        apply_source_corrections(doc, registry)

        formula = doc["elements"][0]
        self.assertEqual(formula["quality_status"], "passed")
        self.assertIn("agent_verified_correction", formula["policy_reasons"])
        self.assertNotIn("owner_verified_correction", formula["policy_reasons"])
        self.assertIn("source_correction:ffffffffffff:agent_reviewed", formula["quality_issues"])

    def test_correction_that_no_longer_matches_fails_the_assembly(self):
        doc = _document()
        doc["elements"][0]["content"]["latex"] = "Q=0"

        with self.assertRaises(SourceCorrectionError):
            apply_source_corrections(doc, _registry(texts=[]))

    def test_bbox_picks_one_of_repeated_lines_on_a_page(self):
        doc = _document()
        doc["elements"] += [
            {"element_id": f"e{n}", "type": "text", "quality_status": "needs_review",
             "quality_issues": [], "content": {"text": "式中："},
             "source_spans": _span(22, [80.0, y, 120.0, y + 12.0])}
            for n, y in ((4, 300.0), (5, 500.0))
        ]
        line = {"source_sha256": SHA, "physical_page": 22, "original_text_sha256": _sha("式中："),
                "corrected_text": "式中："}

        with self.assertRaises(SourceCorrectionError):
            apply_source_corrections(doc, _registry(formulas=[], texts=[line]))
        doc = _document()
        doc["elements"] += [
            {"element_id": f"e{n}", "type": "text", "quality_status": "needs_review",
             "quality_issues": [], "content": {"text": "式中："},
             "source_spans": _span(22, [80.0, y, 120.0, y + 12.0])}
            for n, y in ((4, 300.0), (5, 500.0))
        ]
        apply_source_corrections(doc, _registry(
            owner_verified=True, formulas=[], texts=[{**line, "bbox": [80.0, 500.0, 120.0, 512.0]}]))

        statuses = {e["element_id"]: e["quality_status"] for e in doc["elements"]}
        self.assertEqual((statuses["e4"], statuses["e5"]), ("needs_review", "passed"))

    def test_excluded_line_leaves_retrieval(self):
        doc = _document()
        doc["elements"][1]["role"] = "paragraph"
        apply_source_corrections(doc, _registry(formulas=[], texts=[
            {"source_sha256": SHA, "physical_page": 22, "original_text_sha256": _sha(VARS_WRONG),
             "corrected_text": VARS_WRONG, "exclude": True}]))

        self.assertEqual(doc["elements"][1]["role"], "unknown")

    def test_other_documents_and_no_registry_are_untouched(self):
        doc = _document()
        doc["source"]["sha256"] = "b" * 64

        self.assertEqual(apply_source_corrections(doc, _registry()), 0)
        self.assertEqual(apply_source_corrections(_document(), None), 0)

    def test_loader_records_the_registry_hash(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source-corrections.json"
            path.write_text(json.dumps({"schema_version": "1", "formulas": [], "texts": []}),
                            encoding="utf-8")

            registry = load_source_corrections(path)

            self.assertEqual(registry["registry_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIsNone(load_source_corrections(Path(directory) / "missing.json"))


class CorrectionsDeriveNewVersionTest(unittest.TestCase):
    setUp = t07.RecoveryTextOverrideTests.setUp
    _write_inventory = t07.RecoveryTextOverrideTests._write_inventory
    _service = t07.RecoveryTextOverrideTests._service

    def test_registry_changes_the_corpus_version_only_when_present(self):
        self._write_inventory()
        service, _ = self._service(recovery=False)
        plain = service.build()["corpus_version"]
        registry = self.root / "data/registry/source-corrections.json"

        registry.write_text('{"schema_version": "1", "formulas": [], "texts": []}', encoding="utf-8")
        corrected = service.build()["corpus_version"]
        registry.unlink()

        self.assertNotEqual(corrected, plain)
        self.assertEqual(service.build()["corpus_version"], plain)


if __name__ == "__main__":
    unittest.main()
