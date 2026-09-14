import unittest
from pathlib import Path

from src.ingestion.native_pdf import NativePdfParser


ROOT = Path(__file__).resolve().parents[1]
READABLE_NATIVE_PDF = (
    ROOT
    / "data"
    / "raw"
    / "标准和规范&参考资料"
    / "住房城乡建设部等5部门关于加强城市生活污水管网建设和运行维护的通知_国务院部门文件_中国政府网.pdf"
)
MOJIBAKE_NATIVE_PDF = (
    ROOT
    / "data"
    / "raw"
    / "标准和规范&参考资料"
    / "T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf"
)


class NativePdfParserContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not READABLE_NATIVE_PDF.exists():
            raise unittest.SkipTest("local approved M2 PDF fixture is not available")

    def test_native_page_preserves_text_location_and_raw_evidence(self):
        page = NativePdfParser().parse_page(READABLE_NATIVE_PDF, physical_page=1)

        self.assertEqual(page["physical_page"], 1)
        self.assertEqual(page["page_index"], 0)
        self.assertEqual(page["extraction_route"], "native")
        self.assertEqual(page["coordinate_origin"], "top_left")
        self.assertGreater(page["width"], 0)
        self.assertGreater(page["height"], 0)
        self.assertIn("住房城乡建设部等5部门", page["text"])
        self.assertIn("城市生活污水管网", page["text"])
        self.assertEqual(page["quality"]["status"], "pass")
        self.assertTrue(page["elements"])
        self.assertIn("raw_dict", page["raw"])
        self.assertIn("blocks", page["raw"])

        for order, element in enumerate(page["elements"]):
            self.assertEqual(element["reading_order"], order)
            x0, y0, x1, y1 = element["bbox"]
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page["width"])
            self.assertLessEqual(y1, page["height"])
            self.assertLess(x0, x1)
            self.assertLess(y0, y1)
            self.assertTrue(element["text"].strip())

    def test_valid_unicode_substitutions_are_preserved_for_anchor_evaluation(self):
        page = NativePdfParser().parse_page(MOJIBAKE_NATIVE_PDF, physical_page=10)

        self.assertEqual(page["quality"]["status"], "pass")
        self.assertIn("1. o. 1", page["text"])
        self.assertNotIn("1.0.1", page["text"])

    def test_physical_page_outside_document_is_rejected(self):
        parser = NativePdfParser()
        with self.assertRaisesRegex(ValueError, "physical_page"):
            parser.parse_page(READABLE_NATIVE_PDF, physical_page=0)
        with self.assertRaisesRegex(ValueError, "physical_page"):
            parser.parse_page(READABLE_NATIVE_PDF, physical_page=10_000)


if __name__ == "__main__":
    unittest.main()
