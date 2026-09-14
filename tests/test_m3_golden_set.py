import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SET = ROOT / "data" / "evaluation" / "golden-set-v1.json"
EVIDENCE = ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"


class M3GoldenSetContractTest(unittest.TestCase):
    def setUp(self):
        self.golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8-sig"))
        self.cases = self.golden["cases"]
        self.evidence_ids = {
            json.loads(line)["evidence_id"]
            for line in EVIDENCE.read_text(encoding="utf-8").splitlines()
        }

    def test_v1_is_approved_and_separates_question_origins(self):
        self.assertEqual(self.golden["golden_set_id"], "golden-set-v1")
        self.assertEqual(self.golden["status"], "human_approved")
        self.assertTrue(
            all(case["evidence_review_status"] == "human_approved" for case in self.cases)
        )
        self.assertEqual(len(self.cases), 12)
        self.assertEqual(
            sum(case["origin"] == "user_provided" for case in self.cases), 2
        )
        self.assertEqual(
            sum(case["origin"] == "ai_supplement" for case in self.cases), 10
        )
        self.assertEqual(
            len({case["case_id"] for case in self.cases}), len(self.cases)
        )

    def test_every_expected_evidence_id_exists_in_the_fixed_corpus(self):
        for case in self.cases:
            referenced = case["required_evidence_ids"] + case["acceptable_evidence_ids"]
            self.assertTrue(set(referenced).issubset(self.evidence_ids), case["case_id"])
            if case["expected_outcome"].startswith("no_answer"):
                self.assertEqual(case["required_evidence_ids"], [])

    def test_real_questions_preserve_clarification_and_corpus_gap_behavior(self):
        rainfall = next(case for case in self.cases if case["case_id"] == "REAL-001")
        self.assertEqual(rainfall["expected_outcome"], "needs_clarification")
        self.assertIn("statistical_period", rainfall["missing_conditions"])
        shanxi = next(case for case in self.cases if case["case_id"] == "REAL-002")
        self.assertEqual(shanxi["expected_outcome"], "no_answer_corpus_gap")
        self.assertEqual(shanxi["required_filters"]["jurisdiction"], "山西")
        self.assertEqual(shanxi["required_evidence_ids"], [])
        formula = next(case for case in self.cases if case["case_id"] == "AI-010")
        self.assertEqual(formula["expected_outcome"], "locate_source_only")
        self.assertEqual(
            formula["required_evidence_ids"],
            ["ev_58337104c115f4ddb491ad736f859ae7"],
        )


if __name__ == "__main__":
    unittest.main()
