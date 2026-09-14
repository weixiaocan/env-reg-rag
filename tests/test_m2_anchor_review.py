import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANCHOR_FILE = ROOT / "data" / "registry" / "m2-anchor-review-v0.json"
SAMPLE_FILE = ROOT / "data" / "registry" / "m2-page-sample-v0.csv"
REVIEW_FILE = ROOT / "docs" / "evaluation" / "M2_ANCHOR_REVIEW.md"


class M2AnchorReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))
        cls.anchors = cls.payload["anchors"]
        cls.sample_ids = {
            line.split(",", 1)[0]
            for line in SAMPLE_FILE.read_text(encoding="utf-8-sig").splitlines()[1:]
            if line.strip()
        }

    def test_fixed_anchor_counts(self):
        counts = Counter(anchor["anchor_type"] for anchor in self.anchors)
        self.assertEqual(len(self.anchors), 30)
        self.assertEqual(counts["text_or_clause"], 20)
        self.assertEqual(counts["table_or_numeric"], 10)
        self.assertEqual(self.payload["counts"]["total"], 30)

    def test_ids_are_unique_and_samples_exist(self):
        anchor_ids = [anchor["anchor_id"] for anchor in self.anchors]
        self.assertEqual(len(anchor_ids), len(set(anchor_ids)))
        self.assertTrue(all(anchor["sample_id"] in self.sample_ids for anchor in self.anchors))

    def test_every_anchor_is_human_approved(self):
        self.assertEqual(self.payload["status"], "human_approved")
        self.assertTrue(
            all(anchor["review_status"] == "human_approved" for anchor in self.anchors)
        )
        self.assertEqual(
            set(self.payload["review_summary"]["corrected_anchor_ids"]),
            {"TXT-01", "TXT-06", "TXT-07", "TXT-11", "TAB-10"},
        )

    def test_human_corrections_preserve_full_text_and_table_structure(self):
        by_id = {anchor["anchor_id"]: anchor for anchor in self.anchors}
        self.assertIn("（经融化后）降水", by_id["TXT-01"]["proposed_truth"])
        self.assertIn("小雨情形下", by_id["TXT-06"]["proposed_truth"])
        self.assertIn("连续24h以上", by_id["TXT-06"]["proposed_truth"])
        self.assertIn("硬度是表征地下水的特征因子之一", by_id["TXT-07"]["proposed_truth"])
        self.assertIn("73%以上，城市生活污水收集处理", by_id["TXT-11"]["proposed_truth"])
        self.assertIn("表头：评价对象｜一级指标｜二级指标｜分值｜总分", by_id["TAB-10"]["proposed_truth"])
        self.assertIn("地块雨水接出口晴天出流20分", by_id["TAB-10"]["proposed_truth"])
        self.assertIn("加分项总分10分", by_id["TAB-10"]["proposed_truth"])

    def test_required_traceability_fields_are_present(self):
        required = {
            "anchor_id",
            "anchor_type",
            "sample_id",
            "file_name",
            "pdf_page",
            "printed_page",
            "structure_path",
            "proposed_truth",
            "check_focus",
            "review_status",
        }
        for anchor in self.anchors:
            self.assertTrue(required.issubset(anchor), anchor["anchor_id"])
            self.assertGreater(anchor["pdf_page"], 0)
            self.assertTrue(anchor["proposed_truth"].strip())

    def test_human_review_sheet_lists_every_anchor_once(self):
        review_text = REVIEW_FILE.read_text(encoding="utf-8")
        for anchor in self.anchors:
            marker = f"| {anchor['anchor_id']} |"
            self.assertEqual(review_text.count(marker), 1, anchor["anchor_id"])


if __name__ == "__main__":
    unittest.main()
