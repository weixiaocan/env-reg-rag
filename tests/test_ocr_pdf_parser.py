import unittest
from pathlib import Path

from src.ingestion.ocr_pdf import OcrPdfParser


ROOT = Path(__file__).resolve().parents[1]
SCANNED_PDF = (
    ROOT
    / "data"
    / "raw"
    / "标准和规范&参考资料"
    / "GBT 28592-2012降水量等级.pdf"
)


class FixtureStructureEngine:
    """Deterministic stand-in for the external PaddleOCR model boundary."""

    name = "fixture-structure-engine"
    version = "test"

    def predict(self, image):
        height, width = image.shape[:2]
        return {
            "width": width,
            "height": height,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "第二段",
                    "block_bbox": [0, height / 2, width, height],
                    "block_id": 9,
                    "block_order": 1,
                },
                {
                    "block_label": "doc_title",
                    "block_content": "第一段",
                    "block_bbox": [0, 0, width, height / 2],
                    "block_id": 3,
                    "block_order": 0,
                },
            ],
            "overall_ocr_res": {
                "rec_texts": ["第一段", "第二段"],
                "rec_scores": [0.99, 0.95],
                "rec_boxes": [[0, 0, width, height / 2], [0, height / 2, width, height]],
            },
            "table_res_list": [
                {
                    "table_region_id": 1,
                    "pred_html": "<table><tr><td>地块项目</td><td>100</td></tr></table>",
                    "cell_box_list": [[0, 0, width / 2, height / 4]],
                }
            ],
            "model_settings": {"use_table_recognition": True},
        }


class LowConfidenceFixtureEngine(FixtureStructureEngine):
    def predict(self, image):
        result = super().predict(image)
        result["overall_ocr_res"]["rec_scores"] = [0.99, 0.60]
        return result


class MostlyHighConfidenceFixtureEngine(FixtureStructureEngine):
    def predict(self, image):
        result = super().predict(image)
        result["overall_ocr_res"]["rec_texts"] = [f"第{i}行" for i in range(10)]
        result["overall_ocr_res"]["rec_scores"] = [0.95] * 9 + [0.60]
        return result


class OcrPdfParserContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCANNED_PDF.exists():
            raise unittest.SkipTest("local approved M2 PDF fixture is not available")

    def test_ocr_page_preserves_reading_order_pdf_coordinates_and_tables(self):
        page = OcrPdfParser(engine=FixtureStructureEngine(), dpi=144).parse_page(
            SCANNED_PDF,
            physical_page=4,
        )

        self.assertEqual(page["physical_page"], 4)
        self.assertEqual(page["page_index"], 3)
        self.assertEqual(page["extraction_route"], "full_ocr")
        self.assertEqual(page["coordinate_origin"], "top_left")
        self.assertEqual(page["text"], "第一段\n第二段")
        self.assertEqual([item["reading_order"] for item in page["elements"]], [0, 1])
        self.assertEqual([item["type"] for item in page["elements"]], ["doc_title", "text"])
        self.assertAlmostEqual(page["elements"][0]["bbox"][2], page["width"], places=3)
        self.assertAlmostEqual(
            page["elements"][0]["bbox"][3], page["height"] / 2, places=3
        )
        self.assertEqual(page["tables"][0]["text"], "地块项目 100")
        self.assertIn("pred_html", page["tables"][0]["raw"])
        self.assertEqual(page["quality"]["status"], "pass")
        self.assertAlmostEqual(page["quality"]["metrics"]["mean_confidence"], 0.97)
        self.assertIn("engine_result", page["raw"])

    def test_physical_page_outside_document_is_rejected_before_ocr(self):
        parser = OcrPdfParser(engine=FixtureStructureEngine())
        with self.assertRaisesRegex(ValueError, "physical_page"):
            parser.parse_page(SCANNED_PDF, physical_page=0)
        with self.assertRaisesRegex(ValueError, "physical_page"):
            parser.parse_page(SCANNED_PDF, physical_page=10_000)

    def test_one_noisy_line_does_not_quarantine_an_otherwise_clear_page(self):
        page = OcrPdfParser(engine=MostlyHighConfidenceFixtureEngine()).parse_page(
            SCANNED_PDF, physical_page=4
        )

        self.assertEqual(page["quality"]["status"], "pass")
        self.assertEqual(page["quality"]["metrics"]["low_confidence_ratio"], 0.1)

    def test_low_confidence_text_is_sent_to_review_instead_of_auto_passed(self):
        page = OcrPdfParser(engine=LowConfidenceFixtureEngine()).parse_page(
            SCANNED_PDF, physical_page=4
        )

        self.assertEqual(page["quality"]["status"], "review")
        self.assertIn("low_confidence_text", page["quality"]["reasons"])


if __name__ == "__main__":
    unittest.main()
