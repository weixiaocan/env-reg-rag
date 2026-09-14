import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.canonical_overlays import build_canonical_page_overlays


ROOT = Path(__file__).resolve().parents[1]


class M2CanonicalOverlayContractTest(unittest.TestCase):
    def test_builds_one_traceable_overlay_per_sample_page(self):
        canonical_path = (
            ROOT / "data" / "canonical" / "m2-canonical-sample-v1-documents.jsonl"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = build_canonical_page_overlays(
                ROOT,
                canonical_path,
                Path(directory),
                dpi=72,
            )

            self.assertEqual(result["summary"]["page_count"], 40)
            self.assertEqual(result["summary"]["overlay_count"], 40)
            self.assertEqual(len(result["pages"]), 40)
            self.assertEqual(len({item["sample_id"] for item in result["pages"]}), 40)
            for item in result["pages"]:
                output_path = Path(directory) / item["overlay_file"]
                self.assertTrue(output_path.exists())
                self.assertGreater(output_path.stat().st_size, 0)
            p024 = next(item for item in result["pages"] if item["sample_id"] == "M2-P024")
            self.assertEqual(p024["decision_status"], "quarantine")
            self.assertFalse(p024["publishable"])

            manifest = json.loads(
                (Path(directory) / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["summary"]["overlay_count"], 40)


if __name__ == "__main__":
    unittest.main()
