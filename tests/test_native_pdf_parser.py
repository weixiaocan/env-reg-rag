import unittest
import tempfile
import hashlib
from pathlib import Path

import pymupdf

from src.application.corpus_update import AutomaticPageExtractor, ContentAsset
from src.ingestion.native_pdf import NativePdfParser, apply_scanned_text_layer_routes


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


class ScannedTextLayerRoutingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data" / "raw").mkdir(parents=True)

    def _asset(self, name: str, *, image_rect, render_mode: int) -> ContentAsset:
        image_pdf = pymupdf.open()
        image_page = image_pdf.new_page(width=300, height=400)
        image_page.draw_rect((0, 0, 300, 400), color=(0, 0, 0), fill=(0.95, 0.95, 0.95))
        pixmap = image_page.get_pixmap()
        image_pdf.close()

        pdf = pymupdf.open()
        page = pdf.new_page(width=300, height=400)
        page.insert_image(image_rect, pixmap=pixmap)
        page.insert_text(
            (30, 80),
            "embedded OCR text layer with enough characters",
            render_mode=render_mode,
        )
        path = self.root / "data" / "raw" / name
        pdf.save(path)
        pdf.close()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return ContentAsset(digest, f"data/raw/{name}", name, 1, (), {})

    def test_full_page_scan_with_invisible_text_layer_routes_for_three_source_ocr(self):
        asset = self._asset(
            "scan-with-text.pdf", image_rect=(0, 0, 300, 400), render_mode=3)
        parser = AutomaticPageExtractor(project_root=self.root, enable_ocr=False)

        page = parser.extract(asset, physical_page=1)

        self.assertEqual(page["extraction_route"], "scanned_with_text_layer")
        self.assertEqual(page["decision_status"], "quarantine")
        self.assertIn("scanned_with_text_layer_requires_ocr_vote", page["decision_reasons"])

    def test_small_embedded_image_does_not_change_native_route(self):
        asset = self._asset(
            "native-small-image.pdf", image_rect=(20, 150, 140, 270), render_mode=3)

        page = NativePdfParser().parse_page(
            self.root / asset.canonical_rel_path, physical_page=1)

        self.assertEqual(page["extraction_route"], "native")

    def test_full_page_background_with_visible_vector_text_stays_native(self):
        asset = self._asset(
            "native-background.pdf", image_rect=(0, 0, 300, 400), render_mode=0)

        page = NativePdfParser().parse_page(
            self.root / asset.canonical_rel_path, physical_page=1)

        self.assertEqual(page["extraction_route"], "native")

    def test_existing_page_intermediate_is_reclassified_without_reextracting(self):
        asset = self._asset(
            "legacy-scan-with-text.pdf", image_rect=(0, 0, 300, 400), render_mode=3)
        page_document = {
            "pages": [{
                "physical_page": 1,
                "extraction_route": "native",
                "decision_status": "approved",
                "decision_reasons": [],
            }],
        }

        evidence = apply_scanned_text_layer_routes(
            page_document, self.root / asset.canonical_rel_path)

        self.assertEqual(list(evidence), [1])
        self.assertEqual(
            page_document["pages"][0]["extraction_route"],
            "scanned_with_text_layer",
        )
        self.assertEqual(page_document["pages"][0]["decision_status"], "quarantine")


if __name__ == "__main__":
    unittest.main()
