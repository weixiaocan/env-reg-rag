import json
import re
import unittest
from pathlib import Path

from src.evaluation.table_assertions import evaluate_table_assertion


ROOT = Path(__file__).resolve().parents[1]


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TableAnchorAssertionsTest(unittest.TestCase):
    def test_assertions_decompose_all_approved_table_anchors_without_new_facts(self):
        approved = json.loads(
            (ROOT / "data" / "registry" / "m2-anchor-review-v0.json").read_text(
                encoding="utf-8-sig"
            )
        )
        assertions = json.loads(
            (
                ROOT
                / "data"
                / "registry"
                / "m2-table-anchor-assertions-v1.json"
            ).read_text(encoding="utf-8-sig")
        )
        table_anchors = {
            item["anchor_id"]: item
            for item in approved["anchors"]
            if item["anchor_type"] == "table_or_numeric"
        }

        self.assertEqual(assertions["source_status"], "human_approved")
        self.assertEqual(len(assertions["anchors"]), 10)
        self.assertEqual(
            {item["anchor_id"] for item in assertions["anchors"]},
            set(table_anchors),
        )
        for item in assertions["anchors"]:
            truth = normalized(table_anchors[item["anchor_id"]]["proposed_truth"])
            self.assertEqual(item["sample_id"], table_anchors[item["anchor_id"]]["sample_id"])
            self.assertTrue(item["checks"])
            for check in item["checks"]:
                self.assertTrue(check["required_fragments"])
                for fragment in check["required_fragments"]:
                    self.assertIn(normalized(fragment), truth)

    def test_fragment_evaluator_handles_ocr_formatting_but_keeps_key_typos_visible(self):
        rain_assertion = {
            "anchor_id": "TAB-01",
            "checks": [
                {
                    "check_id": "TAB-01-C01",
                    "required_fragments": ["12 h", "5.0～14.9", "mm"],
                },
                {
                    "check_id": "TAB-01-C02",
                    "required_fragments": ["24 h", "10.0～24.9", "mm"],
                },
            ],
        }
        rain_page = {
            "text": "单位为毫米 12h降雨量 24h降雨量 中雨 5.0~14.9 10.0~24.9",
            "tables": [],
        }
        rain_result = evaluate_table_assertion(rain_page, rain_assertion)
        self.assertTrue(rain_result["all_checks_passed"])

        roman_result = evaluate_table_assertion(
            {"text": "评分项 Ⅰ基础工作 Ⅱ建设效果", "tables": []},
            {
                "anchor_id": "TAB-ROMAN",
                "checks": [
                    {
                        "check_id": "TAB-ROMAN-C01",
                        "required_fragments": ["I基础工作", "II建设效果"],
                    }
                ],
            },
        )
        self.assertTrue(roman_result["all_checks_passed"])

        typo_assertion = {
            "anchor_id": "TAB-10",
            "table_selector_fragments": ["地块项目"],
            "checks": [
                {
                    "check_id": "TAB-10-C03",
                    "required_fragments": ["管网竣工数据资料", "10分"],
                }
            ],
        }
        typo_page = {
            "text": "",
            "tables": [
                {"text": "地块项目 管网峻工数据资料 10"},
                {"text": "市政道路项目 管网竣工数据资料 10"},
            ],
        }
        typo_result = evaluate_table_assertion(typo_page, typo_assertion)
        self.assertFalse(typo_result["all_checks_passed"])
        self.assertEqual(
            typo_result["checks"][0]["missing_fragments"], ["管网竣工数据资料"]
        )


if __name__ == "__main__":
    unittest.main()
