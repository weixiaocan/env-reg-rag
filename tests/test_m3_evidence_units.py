import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.evidence_builder import build_evidence_units


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data" / "canonical" / "m2-canonical-sample-v1-documents.jsonl"


class M3EvidenceUnitContractTest(unittest.TestCase):
    def test_ocr_numeric_ranges_remain_with_their_table_context(self):
        document = {
            "schema_version": "1",
            "asset_id": "asset_ocr_rainfall",
            "document_version_id": "doc_ocr_rainfall",
            "file_name": "GBT 28592-2012降水量等级.pdf",
            "source_uri": "https://example.org/rainfall.pdf",
            "processing_run_id": "run-test",
            "metadata": {},
            "pages": [
                {
                    "sample_id": "P-ocr-rainfall-0004",
                    "physical_page": 4,
                    "page_index": 3,
                    "publishable": True,
                    "decision_status": "approved",
                    "text": "表1 不同时段的降雨量等级划分表",
                    "tables": [],
                    "elements": [
                        {
                            "element_id": f"e{index}",
                            "reading_order": index,
                            "type": "text",
                            "text": text,
                            "bbox": [0, index, 10, index + 1],
                        }
                        for index, text in enumerate(
                            [
                                "表1 不同时段的降雨量等级划分表",
                                "12h降雨量",
                                "24h降雨量",
                                "中雨",
                                "5.0~14.9",
                                "10.0~24.9",
                                "大雨",
                                "15.0~29.9",
                                "25.0~49.9",
                            ]
                        )
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as canonical_dir, tempfile.TemporaryDirectory(dir=ROOT / "tmp") as output_dir:
            canonical_path = Path(canonical_dir) / "canonical.jsonl"
            canonical_path.write_text(
                json.dumps(document, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            result = build_evidence_units(ROOT, canonical_path, Path(output_dir))

        matching = [
            unit
            for unit in result["evidence_units"]
            if "10.0~24.9" in unit["normalized_text"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertIn("24h降雨量", matching[0]["normalized_text"])
        self.assertIn("中雨", matching[0]["normalized_text"])
        self.assertIn("25.0~49.9", matching[0]["normalized_text"])

    def test_approved_pages_become_stable_citable_units(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = build_evidence_units(ROOT, CANONICAL, Path(first_dir))
            second = build_evidence_units(ROOT, CANONICAL, Path(second_dir))

            self.assertGreater(first["summary"]["evidence_unit_count"], 0)
            self.assertEqual(first["summary"]["source_page_count"], 20)
            self.assertEqual(
                [unit["evidence_id"] for unit in first["evidence_units"]],
                [unit["evidence_id"] for unit in second["evidence_units"]],
            )
            self.assertEqual(
                len({unit["evidence_id"] for unit in first["evidence_units"]}),
                len(first["evidence_units"]),
            )

            sample_ids = {
                sample_id
                for unit in first["evidence_units"]
                for sample_id in unit["locator"]["sample_ids"]
            }
            self.assertIn("M2-P001", sample_ids)
            self.assertNotIn("M2-P004", sample_ids)

            rainfall_table = next(
                unit
                for unit in first["evidence_units"]
                if unit["evidence_type"] == "table"
                and "M2-P001" in unit["locator"]["sample_ids"]
            )
            cell_texts = [cell["text"] for cell in rainfall_table["table"]["cells"]]
            self.assertIn("中雨", cell_texts)
            self.assertIn("10.0~24.9", cell_texts)
            self.assertEqual(rainfall_table["locator"]["physical_pages"], [4])

            clause = next(
                unit
                for unit in first["evidence_units"]
                if "M2-P040" in unit["locator"]["sample_ids"]
                and "6.2.1" in unit["normalized_text"]
            )
            self.assertEqual(clause["evidence_type"], "clause")
            self.assertTrue(clause["locator"]["element_ids"])
            self.assertEqual(clause["context_scope"], "page_local_inferred")

            output_path = Path(first_dir) / "m3-evidence-units-v1.jsonl"
            saved = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(saved, first["evidence_units"])
            manifest = json.loads(
                (Path(first_dir) / "m3-evidence-units-v1.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["summary"]["evidence_unit_count"], len(saved)
            )

    def test_native_page_heading_is_context_not_a_standalone_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_evidence_units(ROOT, CANONICAL, Path(directory))

            p008_units = [
                unit
                for unit in result["evidence_units"]
                if "M2-P008" in unit["locator"]["sample_ids"]
            ]
            self.assertFalse(
                any(unit["normalized_text"] == "1 总则" for unit in p008_units)
            )
            first_body_unit = next(
                unit for unit in p008_units if "为规范城镇排水管道" in unit["normalized_text"]
            )
            self.assertEqual(first_body_unit["heading_path"], ["1 总则"])

    def test_source_blocks_are_not_merged_into_oversized_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_evidence_units(ROOT, CANONICAL, Path(directory))

            largest = max(
                len(unit["normalized_text"])
                for unit in result["evidence_units"]
                if unit["usage_policy"] == "answer_and_citation"
            )
            self.assertLessEqual(largest, 600)

    def test_quarantined_formula_page_is_searchable_only_as_a_source_locator(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_evidence_units(ROOT, CANONICAL, Path(directory))

            p024_units = [
                unit
                for unit in result["evidence_units"]
                if "M2-P024" in unit["locator"]["sample_ids"]
            ]
            self.assertEqual(len(p024_units), 1)
            locator = p024_units[0]
            self.assertEqual(locator["evidence_type"], "source_locator")
            self.assertEqual(locator["usage_policy"], "source_locator_only")
            self.assertEqual(
                locator["text_reliability"], "unverified_automatic_extraction"
            )
            self.assertEqual(locator["quality_status"], "quarantine")
            self.assertEqual(locator["locator"]["physical_pages"], [19])
            self.assertIn("5.2.4", locator["normalized_text"])
            self.assertIn("用水量折算法", locator["normalized_text"])
            self.assertEqual(result["summary"]["locator_source_page_count"], 1)


if __name__ == "__main__":
    unittest.main()
