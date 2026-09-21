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
    formula_pages = load_formula_pages(
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


class TestEndToEndAssembly(unittest.TestCase):
    """Acceptance (1): end-to-end assemble + validate."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = _build_cecs758_v2()

    def test_validate_passes(self):
        # Must not raise.
        validate_document(self.doc)

    def test_pages_count(self):
        self.assertEqual(len(self.doc["pages"]), 75)

    def test_elements_non_empty(self):
        self.assertGreater(len(self.doc["elements"]), 0)

    def test_dual_id_format(self):
        self.assertTrue(
            self.doc["canonical_id"].startswith("canonical-sha256:")
        )
        self.assertEqual(len(self.doc["canonical_id"].split(":")[1]), 64)
        self.assertTrue(
            self.doc["canonical_content_id"].startswith("canonical-content-sha256:")
        )
        self.assertEqual(len(self.doc["canonical_content_id"].split(":")[1]), 64)
        self.assertTrue(self.doc["metadata_fingerprint"].startswith("metadata-sha256:"))
        self.assertEqual(len(self.doc["metadata_fingerprint"].split(":")[1]), 64)

    def test_source_and_document_id(self):
        self.assertEqual(self.doc["document_id"], f"pdf-sha256:{CECS758_SHA}")
        self.assertEqual(self.doc["source"]["sha256"], CECS758_SHA)
        self.assertEqual(self.doc["source"]["page_count"], 75)

    def test_metadata_strong_typed(self):
        md = self.doc["metadata"]
        self.assertEqual(md["document_kind"], "standard")
        self.assertEqual(md["effective_status"], "unknown")
        self.assertIn("T/CECS 758-2020", md["identifiers"][0]["value"])
        self.assertEqual(md["language"], "zh-CN")

    def test_all_pages_parsed(self):
        # CECS758 is fully native approved -> every page parsed.
        for p in self.doc["pages"]:
            self.assertEqual(p["parse_status"], "parsed")


class TestDualIdDecoupling(unittest.TestCase):
    """Acceptance (3): metadata change flips canonical_id but not content_id."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = _build_cecs758_v2()

    def test_metadata_change_only_flips_canonical_id(self):
        doc = json.loads(json.dumps(self.base))
        doc["metadata"]["title"] = "tampered title for decoupling test"
        # Clear IDs and recompute.
        doc["canonical_id"] = "canonical-sha256:x"
        doc["canonical_content_id"] = "canonical-content-sha256:x"
        doc["metadata_fingerprint"] = "metadata-sha256:x"
        ids = compute_ids(doc)
        self.assertNotEqual(ids["canonical_id"], self.base["canonical_id"])
        self.assertNotEqual(ids["metadata_fingerprint"], self.base["metadata_fingerprint"])
        # Content ID must be unchanged (content did not change).
        self.assertEqual(ids["canonical_content_id"], self.base["canonical_content_id"])


class TestEightCaseAssertions(unittest.TestCase):
    """Acceptance (2): the 8 CECS758 pipeline cases vs human_assertions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = _build_cecs758_v2()
        cls.cases = {c["physical_page"]: c for c in _load_cecs758_cases()}

    def _case(self, page: int) -> dict:
        return self.cases[page]

    # -- p10: clause text -----------------------------------------------------
    def test_p10_clause_text(self):
        blob = _page_text_blob(self.doc, 10)
        # Key fragments from TXT-04 expected (allow OCR noise).
        self.assertIn("为规范城镇排水管道", blob)
        self.assertIn("制定本规程", blob)

    # -- p28: clause + table fragments ---------------------------------------
    def test_p28_clause_text(self):
        blob = _page_text_blob(self.doc, 28)
        self.assertIn("泵排系统", blob)
        self.assertIn("单个雨水泵站服务范围为最小评估单元", blob)
        self.assertIn("自流排放系统", blob)

    def test_p28_table_fragments(self):
        cells = []
        for e in _page_elements(self.doc, 28):
            if e["type"] == "table":
                cells.extend(c["text"] for c in e["content"]["cells"])
        blob = "\n".join(cells)
        # TAB-05 derived fragments (lenient substring).
        self.assertIn("接入管管径", blob)
        self.assertIn("mm", blob)

    def test_p28_has_table_element(self):
        tables = [e for e in _page_elements(self.doc, 28) if e["type"] == "table"]
        self.assertGreaterEqual(len(tables), 1)

    # -- p35 / p44: review-only, no human truth -------------------------------
    def test_p35_page_has_content(self):
        # No human_assertions; just require the page contributes elements.
        elems = _page_elements(self.doc, 35)
        self.assertGreater(len(elems), 0)

    def test_p44_page_has_content(self):
        elems = _page_elements(self.doc, 44)
        self.assertGreater(len(elems), 0)

    def test_p35_p44_have_table_or_layout(self):
        for page in (35, 44):
            tables = [e for e in _page_elements(self.doc, page) if e["type"] == "table"]
            elems = _page_elements(self.doc, page)
            # Either a table element or at least text elements on the page.
            self.assertTrue(len(tables) >= 1 or len(elems) >= 1)

    # -- p63: clause text (条文说明 5.2.2) ------------------------------------
    def test_p63_clause_text(self):
        blob = _page_text_blob(self.doc, 63)
        self.assertIn("针对污水干管开展水量、水质", blob)
        self.assertIn("加密检测点位", blob)

    # -- p67: continuation table + clause text --------------------------------
    def test_p67_clause_text(self):
        blob = _page_text_blob(self.doc, 67)
        self.assertIn("硬度是表征地下水的特征因子之一", blob)
        self.assertIn("外来水入渗", blob)

    def test_p67_table3_fragments(self):
        # TAB-06 derived: 上海 / 浅层地下水硬度 / 457 / mg/L / 生活污水硬度 / 162
        cells = []
        for e in _page_elements(self.doc, 67):
            if e["type"] == "table":
                cells.extend(c["text"] for c in e["content"]["cells"])
                if e["content"].get("caption"):
                    cells.append(e["content"]["caption"])
        blob = "\n".join(cells)
        self.assertIn("上海", blob)
        self.assertIn("浅层地下水硬度", blob)
        self.assertIn("457", blob)
        self.assertIn("生活污水硬度", blob)
        self.assertIn("162", blob)

    def test_p67_continuation_table_link_or_merged(self):
        """p67 续表 must either merge with p66 or carry a `continues` link."""
        p67_tables = [e for e in _page_elements(self.doc, 67) if e["type"] == "table"]
        self.assertGreaterEqual(len(p67_tables), 1)
        # Check for a continues link targeting a p66 table, OR a multi-span
        # table whose spans cover both p66 and p67.
        p66_table_ids = {
            e["element_id"] for e in _page_elements(self.doc, 66) if e["type"] == "table"
        }
        found_link = False
        found_merged = False
        for t in p67_tables:
            for link in t["links"]:
                if link["type"] == "continues" and link["target_element_id"] in p66_table_ids:
                    found_link = True
            span_pages = {s["physical_page"] for s in t["source_spans"]}
            if 66 in span_pages and 67 in span_pages:
                found_merged = True
        self.assertTrue(
            found_link or found_merged,
            "p67 续表 must link to a p66 table or merge across pages",
        )

    def test_p67_continuation_cell_subset(self):
        """cecs758-industrial-reference-continuation cell subset (lenient).

        V1 native text garbles the ``>=`` values, so we only assert the clean
        ``指标参数`` column names are present as table cells (>= V1 jsonl baseline).
        """
        cells = []
        for e in _page_elements(self.doc, 67):
            if e["type"] == "table":
                cells.extend(c["text"] for c in e["content"]["cells"])
        joined = "\n".join(cells)
        expected_names = ["总氮", "磷酸盐", "氯化物", "氟化物"]
        hits = sum(1 for name in expected_names if name in joined)
        # At least the clean parameter names must be recoverable.
        self.assertGreaterEqual(hits, 4, f"only {hits}/4 clean names found")

    # -- p24: formula (regression) -------------------------------------------
    def test_p24_formula_element(self):
        formulas = [e for e in _page_elements(self.doc, 24) if e["type"] == "formula"]
        self.assertGreaterEqual(len(formulas), 1)
        f = formulas[0]
        self.assertIsNotNone(f["content"]["latex"])
        self.assertIn("Q=", f["content"]["latex"])
        self.assertIn("\\frac", f["content"]["latex"])

    def test_p24_formula_section_path(self):
        formulas = [e for e in _page_elements(self.doc, 24) if e["type"] == "formula"]
        self.assertGreaterEqual(len(formulas), 1)
        labels = [entry["label"] for entry in formulas[0]["section_path"]]
        self.assertIn("5.4.2", labels)

    def test_p24_formula_primary_span_bbox(self):
        formulas = [e for e in _page_elements(self.doc, 24) if e["type"] == "formula"]
        self.assertGreaterEqual(len(formulas), 1)
        primary = [s for s in formulas[0]["source_spans"] if s["role"] == "primary"]
        self.assertEqual(len(primary), 1)
        expected = [107.4, 65.9, 287.4, 91.3]
        self.assertTrue(
            _bbox_close(primary[0]["bbox"], expected, tol=2.0),
            f"bbox {primary[0]['bbox']} not close to {expected}",
        )

    def test_p24_formula_context(self):
        formulas = [e for e in _page_elements(self.doc, 24) if e["type"] == "formula"]
        self.assertGreaterEqual(len(formulas), 1)
        ctx = formulas[0]["content"].get("context_text") or ""
        # Context should mention 流量 and 修正系数 (may be line-broken in V1).
        self.assertIn("流量", ctx)
        self.assertIn("修正", ctx)
        self.assertIn("系数", ctx)

    # -- p68: figure + formulas (regression) ----------------------------------
    def test_p68_figure_element(self):
        figures = [e for e in _page_elements(self.doc, 68) if e["type"] == "figure"]
        self.assertGreaterEqual(len(figures), 1)
        cap = figures[0]["content"].get("caption") or ""
        self.assertIn("图3", cap)
        self.assertIn("圆形管渠横断面示意图", cap)

    def test_p68_formula_elements(self):
        formulas = [e for e in _page_elements(self.doc, 68) if e["type"] == "formula"]
        self.assertGreaterEqual(len(formulas), 2)
        latexes = [f["content"]["latex"] for f in formulas]
        joined = " ".join(latexes)
        # Circular formula must reference the circular cross-section.
        self.assertTrue(
            any("圆形" in l or "\\frac" in l for l in latexes),
            f"circular formula not found in {latexes}",
        )

    def test_p68_figure_has_asset(self):
        figures = [e for e in _page_elements(self.doc, 68) if e["type"] == "figure"]
        self.assertGreaterEqual(len(figures), 1)
        asset = figures[0]["content"].get("asset")
        self.assertIsNotNone(asset)
        self.assertGreater(asset["pixel_width"], 0)
        self.assertGreater(asset["pixel_height"], 0)
        self.assertEqual(len(asset["sha256"]), 64)


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

    def test_assemble_only_reads_json(self):
        # Smoke: assemble runs against an in-memory dict + dict formula cache.
        page_doc = _stream_find_page_document(CECS758_SHA)
        page_count = len(page_doc.get("pages") or [])
        formula_pages = load_formula_pages(
            CECS758_SHA, range(1, page_count + 1), cache_dir=_FORMULA_CACHE_DIR
        )
        doc = assemble_document(page_doc, formula_pages=formula_pages)
        self.assertEqual(doc["schema_version"], "v2.canonical/1.0")


class TestOutputArtifact(unittest.TestCase):
    """The on-disk artifact produced by the entrypoint script."""

    def test_output_file_exists_and_validates(self):
        if not os.path.isfile(_OUTPUT_JSON):
            self.skipTest("output artifact not generated yet; run scripts/assemble_cecs758_v2.py")
        with open(_OUTPUT_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_document(doc)
        self.assertEqual(len(doc["pages"]), 75)


if __name__ == "__main__":
    unittest.main()
