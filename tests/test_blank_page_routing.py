from __future__ import annotations

import unittest
from pathlib import Path

from src.application.corpus_update import AutomaticPageExtractor, ContentAsset
from src.ingestion.ocr_pdf import OcrPdfParser


ROOT = Path(__file__).resolve().parents[1]
BLANK_PDF = ROOT / "data/raw/标准和规范&参考资料/降雨过程强度等级标准.pdf"


VISUAL_ONLY_PDF = ROOT / "data/raw/_official_verification/GBT-28592-2012-official.pdf"


class EmptyOcrEngine:
    name = "empty-test-engine"
    version = "test"

    def predict(self, image):
        height, width = image.shape[:2]
        return {
            "width": width,
            "height": height,
            "parsing_res_list": [],
            "overall_ocr_res": {
                "rec_texts": [],
                "rec_scores": [],
                "rec_boxes": [],
            },
            "table_res_list": [],
            "model_settings": {},
        }


class BlankPageRoutingTest(unittest.TestCase):
    def test_visually_blank_page_is_quarantined_without_blocking_as_failure(self):
        if not BLANK_PDF.is_file():
            self.skipTest("blank PDF fixture is unavailable")
        extractor = AutomaticPageExtractor(project_root=ROOT, enable_ocr=False)
        extractor.enable_ocr = True
        extractor.ocr = OcrPdfParser(engine=EmptyOcrEngine(), dpi=72)
        asset = ContentAsset(
            sha256="a" * 64,
            canonical_rel_path=BLANK_PDF.relative_to(ROOT).as_posix(),
            display_file_name=BLANK_PDF.name,
            page_count=12,
            source_files=(),
            metadata={},
        )

        page = extractor.extract(asset, physical_page=2)

        self.assertEqual(page["decision_status"], "quarantine")
        self.assertIn("visual_blank_page", page["decision_reasons"])
        self.assertEqual(page["text"], "")

    def test_visual_only_page_is_quarantined_without_becoming_answer_evidence(self):
        if not VISUAL_ONLY_PDF.is_file():
            self.skipTest("visual-only PDF fixture is unavailable")
        extractor = AutomaticPageExtractor(project_root=ROOT, enable_ocr=False)
        extractor.enable_ocr = True
        extractor.ocr = OcrPdfParser(engine=EmptyOcrEngine(), dpi=72)
        asset = ContentAsset(
            sha256="b" * 64,
            canonical_rel_path=VISUAL_ONLY_PDF.relative_to(ROOT).as_posix(),
            display_file_name=VISUAL_ONLY_PDF.name,
            page_count=7,
            source_files=(),
            metadata={},
        )

        page = extractor.extract(asset, physical_page=3)

        self.assertEqual(page["decision_status"], "quarantine")
        self.assertEqual(page["decision_reasons"], ["visual_only_page"])
        self.assertFalse(page["publishable"])
        self.assertEqual(page["text"], "")


if __name__ == "__main__":
    unittest.main()
