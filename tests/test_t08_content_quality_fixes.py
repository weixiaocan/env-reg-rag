"""T08 content-quality fixes: gate false positives, reroute guard, table anchoring.

Three root causes proven in the T08 read-only acceptance of corpus-137e29f91cb4
must be fixed WITHOUT fabricating bboxes or forcing failed regions to pass:

1. ``score_text`` treated TOC dot-leaders as mojibake symbols (symbol_ratio
   ~0.9 is '.' leaders) and treated legitimate non-Chinese-dominant pages
   (English contents, reference lists) as unreadable via a bare ``low_han_ratio``.
2. A native->OCR re-route that lost most of the candidate text was silently
   approved -- 12 pages lost >50% of their text with no marker.
3. The full-page table fallback accepted model tables WITHOUT source-bbox
   anchoring (``bbox_basis: missing``) that the primary path would have
   rejected -- the OCR-geometry fallback (validated detection crop) must be
   used instead. Failed regions must never be flipped to completed by hand.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import pymupdf

from src.application.corpus_update import (
    AutomaticPageExtractor,
    ContentAsset,
    page_extractor_config_hash,
)
from src.application.unified_pdf import UnifiedPageExtractor
from src.ingestion.local_table_process import LocalTableProcess
from src.ingestion.text_quality import score_text


class ScoreTextT08Test(unittest.TestCase):
    def test_toc_dot_leaders_are_not_mojibake(self):
        # faithful shape of the rejected native TOC pages (pdf-verified:
        # 4722c42d p5 carries 2215 native chars that are mostly leaders)
        text = ("目　　次\n前　言 ........ 1\n1　范围 ......... 2\n"
                "2　规范性引用文件 ..... 3\n3　总则 ........ 4\n") * 6
        q = score_text(text)
        self.assertEqual(q["status"], "pass", q)
        self.assertEqual(q["reasons"], [])

    def test_english_contents_page_is_not_unreadable(self):
        # pdf-verified: 4336797d p8 etc. are genuine English contents pages
        text = ("Contents\n1 General provisions 2\n2 Terms 3\n"
                "3.1 General requirements 8\n3.2 Stormwater system 8\n"
                "4 Design flow 11\n") * 5
        q = score_text(text)
        self.assertEqual(q["status"], "pass", q)
        self.assertNotIn("low_han_ratio", q["reasons"])

    def test_reference_list_page_is_not_unreadable(self):
        # pdf-verified: a9ce88d5 p46 -- standards references, han_ratio 0.04
        text = ("参考文献\n注1：[1] GB/T 19000-2016 质量管理体系 基础和术语\n"
                "[2] GB/T 22454-2025 工业用金属丝编织网\n[3] ISO 21931-1\n") * 8
        q = score_text(text)
        self.assertEqual(q["status"], "pass", q)

    def test_real_mojibake_still_fails(self):
        # GB2312-misdecoded style: high 0xA1-0xFF symbol mass, no leaders
        text = '°²»¤§»úµçÏßÂ·°²×° ¹¤³ÌÊ©¹¤¼° ÑéÊÕ¹æ·¶' * 4
        q = score_text(text)
        self.assertEqual(q["status"], "fail")
        self.assertIn("mojibake_symbol_text", q["reasons"])

    def test_garbled_ocr_symbols_still_fail(self):
        text = '!@# $%^ &*() !@# $%^ &*()' * 8
        q = score_text(text)
        self.assertEqual(q["status"], "fail")

    def test_replacement_characters_still_fail(self):
        text = "标准规定防水" + "�" * 40
        q = score_text(text)
        self.assertEqual(q["status"], "fail")


class _StubNativeParser:
    def __init__(self, page):
        self._page = page

    def parse_page(self, path, *, physical_page):
        return {**self._page, "physical_page": physical_page}


class _StubOcrParser:
    def __init__(self, page):
        self._page = page

    def parse_page(self, path, *, physical_page):
        return {**self._page, "physical_page": physical_page}


def _base_page(route, text, *, quality_status="pass", elements=None):
    return {
        "physical_page": 1, "page_index": 0, "display_page_label": None,
        "width": 300.0, "height": 400.0, "rotation": 0,
        "extraction_route": route, "decision_status": "approved",
        "decision_reasons": [], "publishable": True, "text": text,
        "elements": elements or [], "tables": [], "raw_artifact_ref": "",
        "coordinate_normalizations": [],
        "quality": {"status": quality_status,
                    "reasons": [] if quality_status == "pass" else ["low_han_ratio"],
                    "metrics": {"non_whitespace_characters":
                                len("".join(text.split()))}},
        "raw": {},
    }


class RerouteShrinkGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.asset = ContentAsset("a" * 64, "data/raw/x.pdf", "x.pdf", 1, (), {})

    def _extractor(self, native_text, ocr_text, *, native_quality="fail"):
        ex = AutomaticPageExtractor(project_root=self.root, enable_ocr=True)
        ex.native = _StubNativeParser(_base_page(
            "native", native_text, quality_status=native_quality))
        ex.ocr = _StubOcrParser(_base_page("full_ocr", ocr_text))
        return ex

    def test_reroute_losing_half_the_text_is_quarantined(self):
        # T08 finding: native 3001 chars -> OCR 543 chars was silently approved
        native_text = "总则 规范性引用文件 术语和定义 " * 120  # ~2900 non-ws
        ex = self._extractor(native_text, "总则 规范性引用文件")
        page = ex.extract(self.asset, physical_page=1)
        self.assertEqual(page["decision_status"], "quarantine")
        self.assertIn("native_text_rejected_ocr_text_shrunk",
                      page["decision_reasons"])

    def test_reroute_with_comparable_text_stays_approved(self):
        native_text = "总则 规范性引用文件 术语和定义 " * 40
        ocr_text = "总则 规范性引用文件 术语和定义 建设项目 总平面 " * 18
        ex = self._extractor(native_text, ocr_text)
        page = ex.extract(self.asset, physical_page=1)
        self.assertEqual(page["decision_status"], "approved")

    def test_trivially_short_native_does_not_trigger_guard(self):
        # native text below the minimum-native threshold is not "lost content"
        ex = self._extractor("短文本", "较长一些的OCR结果，内容大致完整，不触发守卫。")
        page = ex.extract(self.asset, physical_page=1)
        self.assertEqual(page["decision_status"], "approved")

    def test_config_id_is_bumped_for_the_new_gate_semantics(self):
        # R2b routing is applied to existing artifacts during v3 preparation;
        # the extractor config stays stable so unrelated cached pages are not rebuilt.
        ex = AutomaticPageExtractor(project_root=self.root, enable_ocr=True)
        self.assertIn("auto-page-v9", ex.config_id)
        self.assertIn("native-quality=mojibake-v2", ex.config_id)


class TableFallbackAnchoringTest(unittest.TestCase):
    """The full-page table fallback must meet the SAME acceptance bar as the
    primary cropped path: every detected table region needs a parsed table with
    cells AND a bbox intersecting it. Unanchored model output (bbox None) must
    fall through to the OCR-geometry fallback -- never be hand-marked completed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data/raw").mkdir(parents=True)
        pdf = pymupdf.open()
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 30), "Table 1 header row")
        page.insert_text((20, 200), "cell a1 cell b1 cell c1")
        pdf.save(self.root / "data/raw/t.pdf")
        pdf.close()
        import hashlib
        self.sha = hashlib.sha256(
            (self.root / "data/raw/t.pdf").read_bytes()).hexdigest()
        self.asset = ContentAsset(self.sha, "data/raw/t.pdf", "t.pdf", 1, (), {})
        (self.root / "data/registry").mkdir(parents=True, exist_ok=True)
        import csv
        with (self.root / "data/registry/inventory.csv").open(
                "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["file_name", "rel_path", "sha256", "pages"])
            writer.writeheader()
            writer.writerow({"file_name": "t.pdf", "rel_path": "data/raw/t.pdf",
                             "sha256": self.sha, "pages": 1})
        self.layout = Mock()
        self.layout.predict.side_effect = lambda image: [
            {"label": "table", "score": 0.95,
             "coordinate": [40, 60, 560, 640]}]
        self.text_extractor = AutomaticPageExtractor(
            project_root=self.root, enable_ocr=False)

    def _service(self, tables_stub):
        extractor = UnifiedPageExtractor(
            self.root, self.text_extractor, layout=self.layout,
            formula=Mock(), tables=tables_stub,
            model_config={"fixture": "t08-tables"})
        return self._service_from_extractor(extractor)

    def _service_from_extractor(self, extractor):
        from src.application.corpus_update import CorpusUpdateService
        return CorpusUpdateService(self.root, page_extractor=extractor)
        from src.application.corpus_update import CorpusUpdateService
        return CorpusUpdateService(self.root, page_extractor=extractor)

    def _page_with_region(self, service):
        from src.application.corpus_update import build_source_catalog
        catalog = build_source_catalog(self.root)
        asset = catalog.assets[0]
        key = page_extractor_config_hash(
            service.page_extractor)
        return service._page(asset, key, 1, service.page_extractor)[0]

    def test_unanchored_full_page_model_output_falls_back_to_ocr_geometry(self):
        # model transcription present but coordinates missing (the GB50268
        # signature: cells/html recovered, bbox_basis missing)
        tables = Mock(spec=LocalTableProcess)
        tables.recognize_regions.side_effect = ValueError(
            "table_region_structure_not_recovered")
        tables.parse_page.return_value = {
            "physical_page": 1, "width": 300, "height": 400, "elements": [],
            "raw": None,
            "tables": [{"table_id": "m1", "html": "<table><tr><td>a</td>"
                        "<td>b</td></tr></table>"}]}
        service = self._service(tables)
        page = self._page_with_region(service)
        table_regions = [r for r in page["regions"] if r["kind"] == "table"]
        self.assertTrue(table_regions)
        region = table_regions[0]
        self.assertEqual(region["execution_status"], "completed")
        # anchored on the validated detection crop -- never a fabricated bbox
        self.assertTrue(region["tables"])
        anchored = region["tables"][0]
        self.assertEqual(anchored["bbox_basis"], "known_source_table_crop")
        self.assertEqual(anchored["source_bounds_basis"],
                         "validated_original_pdf_crop")
        self.assertTrue(anchored["cells"])
        routes = [a["route"] for a in anchored.get("processing_attempts", [])]
        self.assertIn("ocr_geometry_fallback", routes)

    def test_anchored_full_page_output_is_still_accepted(self):
        # when the full-page model result IS anchored and covers the region,
        # it must keep being accepted (no over-eager fallback)
        tables = Mock(spec=LocalTableProcess)
        tables.recognize_regions.side_effect = ValueError(
            "table_region_structure_not_recovered")
        full = {
            "physical_page": 1, "width": 300, "height": 400, "elements": [],
            "raw": {"render_width": 300, "render_height": 400},
            "tables": [{"table_id": "m1",
                        "html": "<table><tr><td>a</td><td>b</td></tr></table>",
                        "raw": {"bbox": [30.0, 40.0, 280.0, 320.0],
                                "cell_box_list": []}}]}
        tables.parse_page.return_value = full
        service = self._service(tables)
        page = self._page_with_region(service)
        region = next(r for r in page["regions"] if r["kind"] == "table")
        self.assertEqual(region["execution_status"], "completed")
        self.assertTrue(region["tables"])
        self.assertEqual(region["tables"][0]["bbox_basis"], "model_table_bbox")


if __name__ == "__main__":
    unittest.main()
