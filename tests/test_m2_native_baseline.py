import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.native_baseline import run_native_baseline


ROOT = Path(__file__).resolve().parents[1]


class M2NativeBaselineContractTest(unittest.TestCase):
    def test_fixed_sample_subset_produces_traceable_page_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_native_baseline(
                ROOT,
                Path(directory),
                sample_ids={"M2-P001", "M2-P019"},
            )

            self.assertEqual(result["summary"]["page_count"], 2)
            self.assertEqual(result["summary"]["failed_page_count"], 0)
            self.assertEqual(result["metadata"]["parser_name"], "pymupdf-native")
            self.assertEqual(result["metadata"]["input_manifest_id"], "experiment-sample-v0")

            pages = {page["sample_id"]: page for page in result["pages"]}
            self.assertEqual(pages["M2-P001"]["quality"]["status"], "fail")
            self.assertIn("empty_text", pages["M2-P001"]["quality"]["reasons"])
            self.assertEqual(pages["M2-P019"]["quality"]["status"], "pass")
            self.assertTrue(pages["M2-P019"]["anchor_evaluation"]["all_matched"])

            pages_path = Path(directory) / "m2-native-pymupdf-1.28.2-v1-pages.jsonl"
            saved_pages = [
                json.loads(line)
                for line in pages_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({page["sample_id"] for page in saved_pages}, set(pages))
            self.assertTrue(all(page["document_sha256"] for page in saved_pages))

            self.assertTrue(
                (Path(directory) / "m2-native-pymupdf-1.28.2-v1-run-metadata.json").exists()
            )
            self.assertTrue(
                (Path(directory) / "m2-native-pymupdf-1.28.2-v1-summary.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
