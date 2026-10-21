"""Unit tests for the V2 Canonical Assembly module (plan 1.5).

Run::

    PYTHONPATH=src .venv/Scripts/python.exe -m unittest tests.test_canonical_assembly -v
"""
from __future__ import annotations

import json
import os
import unittest

from src.application.canonical_assembly import (
    CECS758_SHA,
    assemble_document,
    load_formula_pages,
)
from src.application.canonical_validation import compute_ids, validate_document
from src.domain.canonical_document import SchemaError

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAGE_DOCUMENTS = os.path.join(
    _REPO_ROOT, "data", "canonical", "corpus-37456321a968-documents.jsonl"
)
_FORMULA_CACHE_DIR = os.path.join(
    _REPO_ROOT, "data", "model_runtime", "corpus_formulas", "5d3264515515e228"
)
_CASES_JSONL = os.path.join(_REPO_ROOT, "data", "review", "v2", "pdf_pipeline_cases.jsonl")
_OUTPUT_JSON = os.path.join(
    _REPO_ROOT,
    "data",
    "canonical",
    "v2",
    "corpus-37456321a968",
    f"{CECS758_SHA}.canonical.json",
)


def _stream_find_page_document(sha: str) -> dict:
    with open(_PAGE_DOCUMENTS, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("sha256") == sha:
                return obj
    raise FileNotFoundError(f"sha {sha} not found in page-intermediate documents")


def _load_cecs758_cases() -> list[dict]:
    cases: list[dict] = []
    with open(_CASES_JSONL, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("pdf_sha256") == CECS758_SHA:
                cases.append(obj)
    return cases


def _build_cecs758_v2() -> dict:
    """Assemble + compute_ids + validate the CECS758 V2 document (in-memory)."""
    page_doc = _stream_find_page_document(CECS758_SHA)
    page_count = len(page_doc.get("pages") or [])
    formula_pages, _cache_failures = load_formula_pages(
        CECS758_SHA, range(1, page_count + 1), cache_dir=_FORMULA_CACHE_DIR
    )
    doc = assemble_document(page_doc, formula_pages=formula_pages)
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
    return doc


def _page_elements(doc: dict, page: int) -> list[dict]:
    out = []
    for e in doc["elements"]:
        for span in e["source_spans"]:
            if span["physical_page"] == page:
                out.append(e)
                break
    return out


def _page_text_blob(doc: dict, page: int) -> str:
    """Concatenation of all text/table cell content on a physical page."""
    parts: list[str] = []
    for e in _page_elements(doc, page):
        if e["type"] == "text":
            parts.append(e["content"]["text"])
        elif e["type"] == "table":
            for cell in e["content"]["cells"]:
                parts.append(cell["text"])
            if e["content"].get("caption"):
                parts.append(e["content"]["caption"])
        elif e["type"] == "formula":
            if e["content"].get("context_text"):
                parts.append(e["content"]["context_text"])
    return "\n".join(parts)


def _bbox_close(a: list[float], b: list[float], tol: float = 6.0) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class TestNoOcrReRun(unittest.TestCase):
    """Acceptance (4): assembly must not invoke OCR / PDF engines."""

    def test_no_unified_pdf_import(self):
        """Assembly must not import or invoke OCR/PDF engines.

        The engine registry may *record* that V1 used paddleocr (as a version
        string), but the module must not import or call those engines.
        """
        import src.application.canonical_assembly as mod

        with open(mod.__file__, "r", encoding="utf-8") as fh:
            src_text = fh.read()
        # No import statements pulling in OCR / PDF engines.
        self.assertNotRegex(src_text, r"^\s*import\s+(fitz|paddleocr|paddlex)",
                            msg="must not import fitz/paddleocr")
        self.assertNotRegex(src_text, r"^\s*from\s+src\.ingestion\.unified_pdf",
                            msg="must not import unified_pdf")
        self.assertNotIn("unified_pdf.", src_text)

class TestOutputArtifact(unittest.TestCase):
    """The on-disk artifact produced by the entrypoint script."""

    def test_output_file_exists_and_validates(self):
        if not os.path.isfile(_OUTPUT_JSON):
            self.skipTest("output artifact not generated yet; run scripts/assemble_corpus_v2.py")
        with open(_OUTPUT_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_document(doc)
        self.assertEqual(len(doc["pages"]), 75)


def _page_doc_with_recurring_header_footer(*, pages=7, header_text="GB 50268-2008",
                                            footer_text="中国工程建设标准化协会"):
    """Build a synthetic page-intermediate document dict.

    Each page carries: a short text in the top band (header), a short text in
    the bottom band (footer), and a body clause in the middle. Page height
    600pt; header band y0<48 (8%), footer band y1>552 (92%).
    """
    page_rows = []
    for pno in range(1, pages + 1):
        page_rows.append({
            "physical_page": pno,
            "width": 400.0,
            "height": 600.0,
            "rotation": 0,
            "extraction_route": "native",
            "decision_status": "approved",
            "elements": [
                # top-band recurring header
                {"element_id": f"p{pno}-hdr", "text": header_text,
                 "bbox": [100.0, 20.0, 300.0, 40.0]},
                # body clause
                {"element_id": f"p{pno}-body", "text": f"第{pno}条 本条为第{pno}页正文条款内容说明文字。",
                 "bbox": [100.0, 300.0, 300.0, 320.0]},
                # bottom-band recurring footer
                {"element_id": f"p{pno}-ftr", "text": footer_text,
                 "bbox": [100.0, 570.0, 300.0, 590.0]},
            ],
            "regions": [],
        })
    return {
        "sha256": "a" * 64,
        "file_name": "synthetic-recurring.pdf",
        "pages": page_rows,
    }


class HeaderFooterRecurringDetectionTest(unittest.TestCase):
    """Running headers/footers (short text recurring in the same page band
    across >=5 pages) must be tagged role=header/footer and dropped from the
    chunker's retrieval projection via _IGNORED_ROLES."""

    def test_recurring_top_bottom_marked_and_dropped(self):
        page_doc = _page_doc_with_recurring_header_footer()
        doc = assemble_document(page_doc, formula_pages={})
        roles = {e["content"]["text"]: e["role"]
                 for e in doc["elements"] if e["type"] == "text"}
        # recurring header/footer texts get the right role
        self.assertEqual(roles["GB 50268-2008"], "header")
        self.assertEqual(roles["中国工程建设标准化协会"], "footer")
        # body text is not mis-flagged as header/footer
        body_roles = [r for t, r in roles.items() if t.startswith("第")]
        self.assertTrue(body_roles, "expected body elements")
        for r in body_roles:
            self.assertNotIn(r, ("header", "footer", "page_number"))

        # chunker drops header/footer (they never appear in any chunk text)
        from src.application.v2_chunker import chunk_document
        chunks = chunk_document(doc, tokenizer=_CharCountTokenizer(), token_budget=80)
        joined = "\n".join(c.text for c in chunks)
        self.assertNotIn("GB 50268-2008", joined)
        self.assertNotIn("中国工程建设标准化协会", joined)
        # body content is still present
        self.assertIn("正文条款内容", joined)

    def test_single_page_short_title_not_misflagged(self):
        # A short title appearing on only 1 page must NOT become header/footer.
        page_doc = _page_doc_with_recurring_header_footer(
            pages=2, header_text="一次性封面标题文字", footer_text="仅此一页的脚注")
        doc = assemble_document(page_doc, formula_pages={})
        roles = {e["content"]["text"]: e["role"]
                 for e in doc["elements"] if e["type"] == "text"}
        # only 2 pages -> below _RECURRING_MIN_PAGES(5) -> not header/footer
        self.assertNotEqual(roles.get("一次性封面标题文字"), "header")
        self.assertNotEqual(roles.get("仅此一页的脚注"), "footer")


class TocSectionPathTest(unittest.TestCase):
    def test_toc_text_is_retained_but_never_enters_heading_stack(self):
        page_doc = {
            "sha256": "b" * 64,
            "file_name": "synthetic-toc.pdf",
            "pages": [
                {"physical_page": 1, "width": 400.0, "height": 600.0,
                 "rotation": 0, "extraction_route": "native",
                 "decision_status": "approved", "regions": [], "elements": [
                     {"element_id": "toc-title", "text": "目 次", "bbox": [170, 40, 230, 60]},
                     {"element_id": "toc-entry", "text": "4.3 沟槽开挖与支护……（12）",
                      "bbox": [60, 100, 340, 120]},
                     {"element_id": "toc-entry-2", "text": "5 管道施工…… 48",
                      "bbox": [60, 130, 340, 150]},
                 ]},
                {"physical_page": 2, "width": 400.0, "height": 600.0,
                 "rotation": 0, "extraction_route": "native",
                 "decision_status": "approved", "regions": [], "elements": [
                     {"element_id": "body-heading", "text": "4 土石方与地基处理",
                      "bbox": [120, 80, 300, 100]},
                     {"element_id": "body-clause", "text": "4.3.10 沟槽支护应符合以下规定：",
                      "bbox": [60, 150, 340, 170]},
                 ]},
            ],
        }
        doc = assemble_document(page_doc, formula_pages={})
        text_elements = {e["content"]["text"]: e for e in doc["elements"]
                         if e["type"] == "text"}
        self.assertIn("4.3 沟槽开挖与支护……（12）", text_elements)
        self.assertNotEqual(text_elements["4.3 沟槽开挖与支护……（12）"]["role"], "heading")
        body_path = text_elements["4.3.10 沟槽支护应符合以下规定："]["section_path"]
        self.assertEqual([entry["label"] for entry in body_path], ["4", "4.3.10"])
        self.assertNotIn("沟槽开挖与支护……（12）",
                         [entry.get("title") for entry in body_path])


class OcrReadingOrderRegressionTest(unittest.TestCase):
    def test_ocr_bare_chapter_without_space_remains_a_heading(self):
        page_doc = {
            "sha256": "d" * 64,
            "file_name": "synthetic-no-space-heading.pdf",
            "pages": [{
                "physical_page": 1, "width": 420.0, "height": 595.0,
                "rotation": 0, "extraction_route": "full_ocr",
                "decision_status": "quarantine", "regions": [],
                "elements": [
                    {"element_id": "chapter", "text": "4地块项目评价",
                     "bbox": [140, 90, 280, 105]},
                    {"element_id": "section", "text": "4.1评分项",
                     "bbox": [150, 125, 270, 140]},
                ],
            }],
        }
        doc = assemble_document(page_doc, formula_pages={})
        texts = {e["content"]["text"]: e for e in doc["elements"]
                 if e["type"] == "text"}
        self.assertEqual(texts["4地块项目评价"]["role"], "heading")
        self.assertEqual(texts["4地块项目评价"]["section_path"], [
            {"label": "4", "title": "地块项目评价"},
        ])

    def test_ocr_clause_lines_reconstruct_with_spans_and_versioned_provenance(self):
        page_doc = {
            "sha256": "e" * 64,
            "file_name": "synthetic-ocr-paragraphs.pdf",
            "pages": [{
                "physical_page": 1, "width": 420.0, "height": 595.0,
                "rotation": 0, "extraction_route": "full_ocr",
                "decision_status": "quarantine", "regions": [{
                    "region_id": "text-region", "kind": "text",
                    "bbox": [40, 90, 360, 210],
                    "quality_status": "needs_review", "issues": [],
                }], "elements": [
                    {"element_id": "heading", "text": "5.10 质量验收标准",
                     "bbox": [130, 70, 290, 84]},
                    {"element_id": "clause", "text": "5.10.7 钢筋混凝土管接口连接应符合下列规定：",
                     "bbox": [48, 100, 345, 112]},
                    {"element_id": "item", "text": "1 管及管件质量应符合本规范第5.6.1、",
                     "bbox": [65, 120, 345, 132]},
                    {"element_id": "numeric-cont", "text": "5.6.2、5.6.5 和 5.7.1 条的规定；",
                     "bbox": [48, 136, 300, 148]},
                    {"element_id": "next-item", "text": "2 接口材料应符合设计要求；",
                     "bbox": [65, 164, 300, 176]},
                ],
            }],
        }
        doc = assemble_document(page_doc, formula_pages={})
        texts = [e for e in doc["elements"] if e["type"] == "text"]
        by_text = {e["content"]["text"]: e for e in texts}
        merged_text = "1 管及管件质量应符合本规范第5.6.1、\n5.6.2、5.6.5 和 5.7.1 条的规定；"
        self.assertIn(merged_text, by_text)
        merged = by_text[merged_text]
        self.assertEqual([span["role"] for span in merged["source_spans"]],
                         ["primary", "continuation"])
        self.assertEqual(len(merged["source_spans"]), 2)
        self.assertEqual(
            [span["bbox"] for span in merged["source_spans"]],
            [[65.0, 120.0, 345.0, 132.0], [48.0, 136.0, 300.0, 148.0]],
        )
        self.assertIn({
            "operation": "merge", "method": "ocr",
            "engine_id": "ocr-paragraph-reconstruction-1", "confidence": None,
        }, merged["provenance"])
        self.assertIn(
            {"engine_id": "ocr-paragraph-reconstruction-1",
             "name": "ocr-paragraph-reconstruction", "version": "t6c-1"},
            doc["generation"]["engines"],
        )
        self.assertIn("2 接口材料应符合设计要求；", by_text)

    def test_native_page_lines_are_not_reconstructed(self):
        page_doc = {
            "sha256": "f" * 64,
            "file_name": "synthetic-native-lines.pdf",
            "pages": [{
                "physical_page": 1, "width": 420.0, "height": 595.0,
                "rotation": 0, "extraction_route": "native",
                "decision_status": "approved", "regions": [], "elements": [
                    {"element_id": "a", "text": "2.1.10 条文首行",
                     "bbox": [48, 100, 345, 112]},
                    {"element_id": "b", "text": "条文续行。",
                     "bbox": [48, 116, 220, 128]},
                ],
            }],
        }
        doc = assemble_document(page_doc, formula_pages={})
        self.assertEqual(
            [e["content"]["text"] for e in doc["elements"] if e["type"] == "text"],
            ["2.1.10 条文首行", "条文续行。"],
        )

    def test_indented_list_continuations_remain_adjacent_and_inherit_clause(self):
        page_doc = {
            "sha256": "c" * 64,
            "file_name": "synthetic-indented-list.pdf",
            "pages": [{
                "physical_page": 1, "width": 420.0, "height": 595.0,
                "rotation": 0, "extraction_route": "full_ocr",
                "decision_status": "quarantine", "regions": [{
                    "region_id": "list-region", "kind": "text",
                    "bbox": [40, 95, 350, 195],
                    "quality_status": "needs_review", "issues": [],
                }], "elements": [
                    {"element_id": "section", "text": "6.1 评分项",
                     "bbox": [145, 70, 243, 83]},
                    # A non-text layout box with a right-shifted centre used to
                    # combine with short continuation lines and cause a false
                    # two-column classification.
                    {"element_id": "layout", "text": "",
                     "bbox": [214, 90, 335, 210]},
                    {"element_id": "clause", "text": "6.1.8 排水口评价总分值为10分。",
                     "bbox": [48, 100, 338, 112]},
                    {"element_id": "item-1", "text": "1雨水排放口晴天无出流，指标不大于",
                     "bbox": [67, 116, 339, 128]},
                    {"element_id": "cont-1", "text": "2mg/L，不扣分；",
                     "bbox": [48, 132, 120, 144]},
                    {"element_id": "item-2", "text": "2雨水排放口晴天有出流，存在一处",
                     "bbox": [67, 160, 340, 172]},
                    {"element_id": "cont-2", "text": "扣2分，最多扣4分；",
                     "bbox": [48, 176, 157, 188]},
                ],
            }],
        }
        doc = assemble_document(page_doc, formula_pages={})
        text_elements = [e for e in doc["elements"]
                         if e["type"] == "text" and e["content"]["text"]]
        texts = [e["content"]["text"] for e in text_elements]
        expected = [
            "6.1 评分项", "6.1.8 排水口评价总分值为10分。",
            "1雨水排放口晴天无出流，指标不大于\n2mg/L，不扣分；",
            "2雨水排放口晴天有出流，存在一处\n扣2分，最多扣4分；",
        ]
        self.assertEqual(texts, expected)
        by_text = {e["content"]["text"]: e for e in text_elements}
        for continuation in expected[2:]:
            self.assertEqual(
                [entry["label"] for entry in by_text[continuation]["section_path"]],
                ["6.1", "6.1.8"],
            )
            self.assertIsNone(by_text[continuation]["section_path"][-1]["title"])
            self.assertEqual(by_text[continuation]["source_region_ids"], ["list-region"])

    def test_page_boundary_preserves_page_then_vertical_order(self):
        page_doc = {
            "sha256": "d" * 64,
            "file_name": "synthetic-cross-page.pdf",
            "pages": [
                {"physical_page": 1, "width": 420.0, "height": 595.0,
                 "rotation": 0, "extraction_route": "full_ocr",
                 "decision_status": "quarantine", "regions": [], "elements": [
                     {"element_id": "p1-a", "text": "1 第一款首行", "bbox": [67, 500, 339, 512]},
                     {"element_id": "p1-b", "text": "第一页续行", "bbox": [48, 516, 157, 528]},
                 ]},
                {"physical_page": 2, "width": 420.0, "height": 595.0,
                 "rotation": 0, "extraction_route": "full_ocr",
                 "decision_status": "quarantine", "regions": [], "elements": [
                     {"element_id": "p2-a", "text": "第二页续行", "bbox": [48, 70, 157, 82]},
                     {"element_id": "p2-b", "text": "2 第二款首行", "bbox": [67, 86, 339, 98]},
                 ]},
            ],
        }
        doc = assemble_document(page_doc, formula_pages={})
        self.assertEqual(
            [e["content"]["text"] for e in doc["elements"] if e["type"] == "text"],
            ["1 第一款首行\n第一页续行", "第二页续行", "2 第二款首行"],
        )


class _CharCountTokenizer:
    def count(self, text: str) -> int:
        return len(text)

    def encode(self, text: str, **_kwargs):
        return list(text)


if __name__ == "__main__":
    unittest.main()
