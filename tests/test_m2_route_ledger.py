import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.route_ledger import build_route_ledger


ROOT = Path(__file__).resolve().parents[1]


class M2RouteLedgerContractTest(unittest.TestCase):
    def test_fixed_evidence_produces_one_explainable_decision_per_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_route_ledger(ROOT, Path(directory), artifact_prefix="ledger-contract")

            self.assertEqual(result["summary"]["sample_count"], 40)
            self.assertEqual(len({row["sample_id"] for row in result["rows"]}), 40)
            by_sample = {row["sample_id"]: row for row in result["rows"]}

            self.assertEqual(
                (by_sample["M2-P008"]["selected_route"], by_sample["M2-P008"]["decision_status"]),
                ("native", "approved"),
            )
            self.assertEqual(
                (by_sample["M2-P004"]["selected_route"], by_sample["M2-P004"]["decision_status"]),
                ("skip_blank", "skip_blank"),
            )
            self.assertEqual(
                (by_sample["M2-P014"]["selected_parser"], by_sample["M2-P014"]["decision_status"]),
                ("paddleocr-ppstructurev3", "approved"),
            )
            self.assertEqual(by_sample["M2-P024"]["decision_status"], "quarantine")
            self.assertEqual(
                (by_sample["M2-P026"]["selected_route"], by_sample["M2-P026"]["decision_status"]),
                ("docling_recovery", "needs_manual_review"),
            )
            self.assertEqual(
                (by_sample["M2-P036"]["selected_route"], by_sample["M2-P036"]["decision_status"]),
                ("docling_recovery", "approved"),
            )
            self.assertTrue(all(row["decision_reasons"] for row in result["rows"]))

            csv_path = Path(directory) / "ledger-contract.csv"
            metadata_path = Path(directory) / "ledger-contract.meta.json"
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 40)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["summary"], result["summary"])
            self.assertTrue(metadata["input_sha256"])


if __name__ == "__main__":
    unittest.main()
