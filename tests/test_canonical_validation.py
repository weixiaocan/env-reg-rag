"""Unit tests for the V2 Canonical Document validator (plan 1.3).

Covers (plan 1.3 acceptance):
- the 7 real cases from design §11 (blank page, hybrid OCR, merged table,
  continuation table, formula, cross-page formula, figure) validated via
  ``validate_document`` after IDs are filled by ``compute_ids``;
- independent digest verification (tamper hex / tamper prefix);
- digest decoupling (metadata change keeps ``canonical_content_id``);
- one reverse case per invariant (design §12) through ``validate_document``;
- unknown-field rejection at multiple nesting levels;
- span/engine ID resolvability reverse cases.
"""

from __future__ import annotations

import copy
import unittest

from src.domain.canonical_document import SCHEMA_VERSION, SchemaError
from src.application.canonical_validation import (
    compute_ids,
    validate_document,
)

# -- shared hex constants ---------------------------------------------------- #
CONFIG_SHA = "9" * 64
REG_SNAPSHOT_SHA = "r" * 64
ASSET_SHA = "d" * 64

SHA_BLANK = "b1" * 32
SHA_HYBRID = "b2" * 32
SHA_TABLE = "b3" * 32
SHA_SPANTABLE = "b4" * 32
SHA_FORMULA = "b5" * 32
SHA_XPAGE = "b6" * 32
SHA_FIGURE = "b7" * 32


# -- small builders ---------------------------------------------------------- #
def _page(n: int, width: float = 595.2, height: float = 841.8,
          status: str = "parsed", label: str | None = None,
          parse_error: dict | None = None) -> dict:
    return {
        "physical_page": n, "printed_label": label,
        "width": width, "height": height, "unit": "pt",
        "rotation_degrees": 0, "parse_status": status,
        "parse_error": parse_error,
    }


def _span(sid: str, role: str, page: int, bbox: list[float]) -> dict:
    return {"span_id": sid, "role": role, "physical_page": page,
            "bbox": bbox, "orientation_degrees": 0}


def _prov(op: str, method: str, engine_id: str, conf: float | None = None) -> dict:
    return {"operation": op, "method": method, "engine_id": engine_id,
            "confidence": conf}


def _engine(eid: str, name: str, version: str) -> dict:
    return {"engine_id": eid, "name": name, "version": version}


def _doc(*, sha: str, file_name: str, title: str, pages: list,
         elements: list, engines: list, document_kind: str = "standard",
         jurisdictions: list | None = None, identifiers: list | None = None,
         effective_status: str = "current",
         effective_status_as_of: str | None = "2026-09-21") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_id": "canonical-sha256:" + "0" * 64,          # placeholder
        "canonical_content_id": "canonical-content-sha256:" + "0" * 64,
        "metadata_fingerprint": "metadata-sha256:" + "0" * 64,
        "document_id": f"pdf-sha256:{sha}",
        "source": {
            "sha256": sha, "file_name": file_name,
            "media_type": "application/pdf", "page_count": len(pages),
            "source_uri": None,
        },
        "metadata": {
            "title": title, "alternate_titles": [], "language": "zh-CN",
            "identifiers": identifiers or [{"scheme": "standard_number", "value": "UNKNOWN"}],
            "document_kind": document_kind,
            "jurisdictions": jurisdictions if jurisdictions is not None else [],
            "issuing_authorities": [], "publication_date": None,
            "effective_from": None, "effective_to": None,
            "effective_status": effective_status,
            "effective_status_as_of": effective_status_as_of,
            "registry_snapshot_id": "source-registry-sha256:" + REG_SNAPSHOT_SHA,
        },
        "generation": {
            "run_id": "run-v2-1", "created_at": "2026-09-21T00:00:00Z",
            "pipeline": "v2-pdf-pipeline", "pipeline_version": "0.1.0",
            "config_sha256": CONFIG_SHA, "engines": engines,
        },
        "pages": pages, "elements": elements,
    }


def _with_ids(doc: dict) -> dict:
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
    return doc


def _flip_last_hex(s: str) -> str:
    """Return ``s`` with its final hex char replaced by a different hex char."""
    last = s[-1]
    return s[:-1] + ("0" if last != "0" else "1")


# =========================================================================== #
# 7 real cases (design §11)
# =========================================================================== #

def _case_blank_page() -> dict:
    """11.1 空白页 QXT489 p2 — page 2 blank, contributes no elements."""
    pages = [_page(1), _page(2, status="blank")]
    elements = [{
        "element_id": "e000001", "type": "text", "role": "clause",
        "label": "1.1", "section_path": [{"label": "1", "title": "总则"}],
        "content": {"text": "为规范降雨监测，制定本标准。", "list": None},
        "source_spans": [_span("s000001", "primary", 1, [66.0, 170.0, 529.0, 238.0])],
        "provenance": [_prov("extract", "native_text", "native-1", 0.95)],
        "links": [],
    }]
    return _doc(sha=SHA_BLANK, file_name="QXT489.pdf", title="降雨监测标准",
                pages=pages, elements=elements,
                engines=[_engine("native-1", "pymupdf", "1.23")],
                identifiers=[{"scheme": "standard_number", "value": "QXT 489"}])


def _case_hybrid_ocr() -> dict:
    """11.2 Hybrid OCR 条款 QXT489 p9 — native/ocr/hybrid provenance.

    The source page 9 is represented as physical_page=1 (continuous 1..N)
    with the original printed number preserved in ``printed_label``.
    """
    pages = [_page(1, label="9")]
    elements = [{
        "element_id": "e000018", "type": "text", "role": "clause",
        "label": "2.1",
        "section_path": [{"label": "2", "title": "术语和定义"}],
        "content": {"text": "降雨的发生、发展和结束的全部演变过程。", "list": None},
        "source_spans": [_span("s000021", "primary", 1, [66.0, 170.0, 529.0, 238.0])],
        "provenance": [
            _prov("extract", "native_text", "native-1"),
            _prov("recognize", "ocr", "ocr-1"),
            _prov("merge", "hybrid", "pipeline-1"),
        ],
        "links": [],
    }]
    return _doc(sha=SHA_HYBRID, file_name="QXT489.pdf", title="降雨监测标准",
                pages=pages, elements=elements,
                engines=[_engine("native-1", "pymupdf", "1.23"),
                         _engine("ocr-1", "paddleocr", "2.6"),
                         _engine("pipeline-1", "v2-assembly", "0.1.0")],
                identifiers=[{"scheme": "standard_number", "value": "QXT 489"}])


def _case_merged_table() -> dict:
    """11.3 合并表 重庆导则 p15 — rowspan=2 merged header cells.

    Source page 15 -> physical_page=1, printed_label="15".
    """
    pages = [_page(1, label="15")]
    elements = [{
        "element_id": "e000231", "type": "table", "role": "table",
        "label": "表3.2.3",
        "section_path": [{"label": "3.2", "title": "地块项目评价"}],
        "content": {
            "row_count": 12, "column_count": 5,
            "caption": "表3.2.3 地块项目评价指标",
            "cells": [
                {"row": 0, "column": 0, "row_span": 2, "column_span": 1, "role": "header", "text": "评价对象", "source_span_ids": []},
                {"row": 0, "column": 1, "row_span": 2, "column_span": 1, "role": "header", "text": "一级指标", "source_span_ids": []},
                {"row": 0, "column": 2, "row_span": 2, "column_span": 1, "role": "header", "text": "二级指标", "source_span_ids": []},
                {"row": 2, "column": 2, "row_span": 1, "column_span": 1, "role": "data", "text": "管网竣工数据资料", "source_span_ids": []},
            ],
            "notes": [],
        },
        "source_spans": [_span("s000310", "primary", 1, [48.0, 112.0, 361.0, 524.0])],
        "provenance": [_prov("recognize", "table_recognition", "table-1", 0.9)],
        "links": [],
    }]
    return _doc(sha=SHA_TABLE, file_name="重庆导则.pdf", title="地块项目评价导则",
                pages=pages, elements=elements,
                engines=[_engine("table-1", "camelot", "0.11")],
                jurisdictions=["重庆市"])


def _case_continuation_table() -> dict:
    """11.4 续表 CECS758 p67 — one logical table spanning p66+p67.

    Source pages 66-67 -> physical_page 1-2, printed_label "66"/"67".
    """
    pages = [_page(1, label="66"), _page(2, label="67")]
    elements = [{
        "element_id": "e000602", "type": "table", "role": "table",
        "label": "续表", "section_path": [],
        "content": {
            "row_count": 7, "column_count": 2, "caption": None,
            "cells": [
                {"row": 0, "column": 0, "row_span": 1, "column_span": 1, "role": "header", "text": "指标参数", "source_span_ids": ["s000802"]},
                {"row": 0, "column": 1, "row_span": 1, "column_span": 1, "role": "header", "text": "参照值", "source_span_ids": ["s000802"]},
                {"row": 1, "column": 0, "row_span": 1, "column_span": 1, "role": "stub", "text": "总氮", "source_span_ids": ["s000802"]},
                {"row": 1, "column": 1, "row_span": 1, "column_span": 1, "role": "data", "text": ">=100mg/L", "source_span_ids": ["s000802"]},
            ],
            "notes": [],
        },
        "source_spans": [
            _span("s000801", "primary", 1, [41.0, 420.0, 338.0, 560.0]),
            _span("s000802", "continuation", 2, [41.881, 74.539, 337.776, 226.663]),
        ],
        "provenance": [
            _prov("recognize", "table_recognition", "table-1", 0.9),
            _prov("merge", "hybrid", "pipeline-1"),
        ],
        "links": [],
    }]
    return _doc(sha=SHA_SPANTABLE, file_name="CECS758.pdf", title="城镇排水技术规程",
                pages=pages, elements=elements,
                engines=[_engine("table-1", "camelot", "0.11"),
                         _engine("pipeline-1", "v2-assembly", "0.1.0")],
                identifiers=[{"scheme": "standard_number", "value": "CECS 758"}])


def _case_formula() -> dict:
    """11.5 公式 CECS758 p24 — latex + context_text, primary/context spans.

    Source page 24 -> physical_page=1, printed_label="24".
    """
    pages = [_page(1, label="24")]
    elements = [{
        "element_id": "e000407", "type": "formula", "role": "display_formula",
        "label": "5.4.2-2",
        "section_path": [{"label": "5.4.2", "title": None}],
        "content": {
            "formula_number": "5.4.2-2",
            "latex": "Q=\\frac{1}{n}\\sum_{i=1}^{n}A\\times L_i/\\Delta t_i\\times k\\times3600\\times24",
            "recognized_formula": None,
            "context_text": "式中：Q——流量（m³/d）；n——测定次数（次/d）；A——管渠过流面积（m²）；k——修正系数，取0.8～0.9。",
        },
        "source_spans": [
            _span("s000501", "primary", 1, [107.405, 65.946, 287.415, 91.283]),
            _span("s000502", "context", 1, [50.0, 95.0, 350.0, 220.0]),
        ],
        "provenance": [_prov("recognize", "formula_recognition", "formula-1", 0.88)],
        "links": [],
    }]
    return _doc(sha=SHA_FORMULA, file_name="CECS758.pdf", title="城镇排水技术规程",
                pages=pages, elements=elements,
                engines=[_engine("formula-1", "pix2tex", "0.1")],
                identifiers=[{"scheme": "standard_number", "value": "CECS 758"}])


def _case_cross_page_formula() -> dict:
    """11.6 跨页公式说明 GB50014 p61-62 — primary on p61, context on p61+p62.

    Source pages 61-62 -> physical_page 1-2, printed_label "61"/"62".
    """
    pages = [_page(1, label="61"), _page(2, label="62")]
    elements = [{
        "element_id": "e000517", "type": "formula", "role": "display_formula",
        "label": "6.3.1-1",
        "section_path": [{"label": "6.3.1", "title": None}],
        "content": {
            "formula_number": "6.3.1-1",
            "latex": "q=vA",
            "recognized_formula": None,
            "context_text": "式中：q——设计流量（m³/s）；v——流速（m/s）；A——过流面积（m²）。",
        },
        "source_spans": [
            _span("s000601", "primary", 1, [80.0, 100.0, 300.0, 140.0]),
            _span("s000602", "context", 1, [80.0, 150.0, 300.0, 220.0]),
            _span("s000603", "context", 2, [80.0, 100.0, 300.0, 180.0]),
        ],
        "provenance": [_prov("recognize", "formula_recognition", "formula-1", 0.8)],
        "links": [],
    }]
    return _doc(sha=SHA_XPAGE, file_name="GB50014.pdf", title="室外排水设计标准",
                pages=pages, elements=elements,
                engines=[_engine("formula-1", "pix2tex", "0.1")],
                identifiers=[{"scheme": "standard_number", "value": "GB 50014"}])


def _case_figure() -> dict:
    """11.7 图片 CECS758 p68 图3 — asset + caption + reference.

    Source page 68 -> physical_page=1, printed_label="68".
    """
    pages = [_page(1, label="68")]
    elements = [{
        "element_id": "e000711", "type": "figure", "role": "figure",
        "label": "图3", "section_path": [],
        "content": {
            "caption": "图3 圆形管渠横断面示意图",
            "references": [
                {"text": "l——AB的弧长（m）（图3）", "source_span_ids": ["s000902"]},
            ],
            "asset": {
                "ref": "assets/sha256/figure3.png", "sha256": ASSET_SHA,
                "media_type": "image/png", "pixel_width": 1276, "pixel_height": 424,
            },
        },
        "source_spans": [
            _span("s000901", "primary", 1, [90.493, 359.191, 320.117, 435.556]),
            _span("s000902", "context", 1, [60.0, 295.0, 300.0, 455.0]),
        ],
        "provenance": [_prov("crop", "image_extraction", "image-1", None)],
        "links": [],
    }]
    return _doc(sha=SHA_FIGURE, file_name="CECS758.pdf", title="城镇排水技术规程",
                pages=pages, elements=elements,
                engines=[_engine("image-1", "pymupdf-image", "1.23")],
                identifiers=[{"scheme": "standard_number", "value": "CECS 758"}])


_ALL_CASES = [
    _case_blank_page, _case_hybrid_ocr, _case_merged_table,
    _case_continuation_table, _case_formula, _case_cross_page_formula,
    _case_figure,
]


class RealCasesTest(unittest.TestCase):
    """The 7 real cases from design §11 must pass full validation."""

    def test_blank_page_qxt489_p2(self):
        doc = _with_ids(_case_blank_page())
        validate_document(doc)
        # blank page 2 must not contribute any element
        self.assertEqual(len(doc["elements"]), 1)
        self.assertEqual(doc["pages"][1]["parse_status"], "blank")

    def test_hybrid_ocr_qxt489_p9(self):
        doc = _with_ids(_case_hybrid_ocr())
        validate_document(doc)
        prov = doc["elements"][0]["provenance"]
        self.assertEqual([p["method"] for p in prov], ["native_text", "ocr", "hybrid"])

    def test_merged_table_chongqing_p15(self):
        doc = _with_ids(_case_merged_table())
        validate_document(doc)
        cells = doc["elements"][0]["content"]["cells"]
        self.assertEqual(cells[0]["row_span"], 2)  # merged header preserved

    def test_continuation_table_cecs758_p67(self):
        doc = _with_ids(_case_continuation_table())
        validate_document(doc)
        spans = doc["elements"][0]["source_spans"]
        self.assertEqual([s["physical_page"] for s in spans], [1, 2])

    def test_formula_cecs758_p24(self):
        doc = _with_ids(_case_formula())
        validate_document(doc)
        self.assertIsNotNone(doc["elements"][0]["content"]["latex"])

    def test_cross_page_formula_gb50014_p61_62(self):
        doc = _with_ids(_case_cross_page_formula())
        validate_document(doc)
        spans = doc["elements"][0]["source_spans"]
        self.assertEqual([s["physical_page"] for s in spans], [1, 1, 2])

    def test_figure_cecs758_p68(self):
        doc = _with_ids(_case_figure())
        validate_document(doc)
        self.assertEqual(doc["elements"][0]["content"]["asset"]["pixel_width"], 1276)

    def test_all_seven_compute_ids_then_validate(self):
        for builder in _ALL_CASES:
            doc = builder()
            ids = compute_ids(doc)
            # fill and re-validate that compute_ids is stable
            doc.update(ids)
            ids2 = compute_ids(doc)
            self.assertEqual(ids, ids2)
            validate_document(doc)


# =========================================================================== #
# Digest verification
# =========================================================================== #

class DigestVerificationTest(unittest.TestCase):
    def test_tamper_canonical_id_hex(self):
        doc = _with_ids(_case_hybrid_ocr())
        doc["canonical_id"] = _flip_last_hex(doc["canonical_id"])
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_tamper_canonical_content_id_prefix(self):
        doc = _with_ids(_case_hybrid_ocr())
        hexpart = doc["canonical_content_id"].split(":", 1)[1]
        doc["canonical_content_id"] = "canonical-sha256:" + hexpart  # wrong prefix
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_tamper_metadata_fingerprint_hex(self):
        doc = _with_ids(_case_hybrid_ocr())
        doc["metadata_fingerprint"] = _flip_last_hex(doc["metadata_fingerprint"])
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_tamper_canonical_id_bad_prefix(self):
        doc = _with_ids(_case_formula())
        hexpart = doc["canonical_id"].split(":", 1)[1]
        doc["canonical_id"] = "canonical-content-sha256:" + hexpart
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_tamper_non_hex_id(self):
        doc = _with_ids(_case_figure())
        doc["canonical_id"] = "canonical-sha256:" + "z" * 64
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_metadata_change_decouples_content_id(self):
        doc = _with_ids(_case_hybrid_ocr())
        content_before = doc["canonical_content_id"]
        canonical_before = doc["canonical_id"]
        meta_before = doc["metadata_fingerprint"]
        # mutate metadata only
        doc["metadata"]["title"] = "改标题"
        new_ids = compute_ids(doc)
        self.assertEqual(new_ids["canonical_content_id"], content_before)
        self.assertNotEqual(new_ids["canonical_id"], canonical_before)
        self.assertNotEqual(new_ids["metadata_fingerprint"], meta_before)

    def test_content_change_changes_content_id(self):
        doc = _with_ids(_case_hybrid_ocr())
        content_before = doc["canonical_content_id"]
        doc["elements"][0]["content"]["text"] = "降雨的部分过程。"
        new_ids = compute_ids(doc)
        self.assertNotEqual(new_ids["canonical_content_id"], content_before)

    def test_generation_run_id_not_in_digest(self):
        """run_id / created_at must not participate in the canonical digest."""
        doc = _with_ids(_case_hybrid_ocr())
        canonical_before = doc["canonical_id"]
        content_before = doc["canonical_content_id"]
        doc["generation"]["run_id"] = "run-different"
        doc["generation"]["created_at"] = "2020-01-01T00:00:00Z"
        new_ids = compute_ids(doc)
        self.assertEqual(new_ids["canonical_id"], canonical_before)
        self.assertEqual(new_ids["canonical_content_id"], content_before)


# =========================================================================== #
# 17 invariants (design §12) — one reverse case each via validate_document
# =========================================================================== #

def _baseline_full() -> dict:
    """Baseline doc carrying all 4 element types on one parsed page."""
    pages = [_page(1)]
    elements = [
        {
            "element_id": "e000001", "type": "text", "role": "clause",
            "label": "2.1", "section_path": [{"label": "2", "title": "术语"}],
            "content": {"text": "降雨的全部演变过程。", "list": None},
            "source_spans": [_span("s000001", "primary", 1, [10.0, 10.0, 100.0, 50.0])],
            "provenance": [_prov("extract", "native_text", "native-1", 0.9)],
            "links": [],
        },
        {
            "element_id": "e000002", "type": "table", "role": "table",
            "label": "表1", "section_path": [],
            "content": {
                "row_count": 2, "column_count": 2, "caption": "表1 示例",
                "cells": [
                    {"row": 0, "column": 0, "row_span": 1, "column_span": 1, "role": "header", "text": "A", "source_span_ids": []},
                    {"row": 0, "column": 1, "row_span": 1, "column_span": 1, "role": "header", "text": "B", "source_span_ids": []},
                    {"row": 1, "column": 0, "row_span": 1, "column_span": 1, "role": "data", "text": "x", "source_span_ids": []},
                    {"row": 1, "column": 1, "row_span": 1, "column_span": 1, "role": "data", "text": "y", "source_span_ids": []},
                ],
                "notes": [],
            },
            "source_spans": [_span("s000002", "primary", 1, [10.0, 60.0, 200.0, 200.0])],
            "provenance": [_prov("recognize", "table_recognition", "table-1", 0.8)],
            "links": [],
        },
        {
            "element_id": "e000003", "type": "formula", "role": "display_formula",
            "label": "5.4.2-2", "section_path": [{"label": "5.4.2", "title": None}],
            "content": {"formula_number": "5.4.2-2", "latex": "Q=1",
                        "recognized_formula": None, "context_text": "式中：Q——流量。"},
            "source_spans": [
                _span("s000003", "primary", 1, [10.0, 210.0, 100.0, 230.0]),
                _span("s000004", "context", 1, [10.0, 230.0, 200.0, 300.0]),
            ],
            "provenance": [_prov("recognize", "formula_recognition", "formula-1", 0.7)],
            "links": [],
        },
        {
            "element_id": "e000004", "type": "figure", "role": "figure",
            "label": "图3", "section_path": [],
            "content": {
                "caption": "图3 示意图",
                "references": [{"text": "l——弧长（m）（图3）", "source_span_ids": ["s000006"]}],
                "asset": {"ref": "assets/sha256/fig.png", "sha256": ASSET_SHA,
                          "media_type": "image/png", "pixel_width": 100, "pixel_height": 100},
            },
            "source_spans": [
                _span("s000005", "primary", 1, [10.0, 310.0, 200.0, 400.0]),
                _span("s000006", "context", 1, [10.0, 400.0, 200.0, 450.0]),
            ],
            "provenance": [_prov("crop", "image_extraction", "image-1", None)],
            "links": [],
        },
    ]
    return _doc(sha="ab" * 32, file_name="baseline.pdf", title="基线文档",
                pages=pages, elements=elements,
                engines=[_engine("native-1", "pymupdf", "1.23"),
                         _engine("table-1", "camelot", "0.11"),
                         _engine("formula-1", "pix2tex", "0.1"),
                         _engine("image-1", "pymupdf-image", "1.23")])


def _valid() -> dict:
    return _with_ids(_baseline_full())


class InvariantsTest(unittest.TestCase):
    def _assert_invalid(self, mutate):
        doc = _valid()
        mutate(doc)
        with self.assertRaises(SchemaError):
            validate_document(doc)

    # 1. metadata closed schema
    def test_inv1_metadata_unknown_field(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("tags", ["x"]))

    # 2. effective_status non-unknown requires as_of
    def test_inv2_status_without_as_of(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("effective_status_as_of", None))

    # 3. unknown values not guessed (enum enforcement)
    def test_inv3_bad_document_kind(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("document_kind", "law"))

    # 4. pages.length == page_count, continuous and unique
    def test_inv4_page_count_mismatch(self):
        self._assert_invalid(lambda d: d["source"].__setitem__("page_count", 2))

    # 5. elements[] is the sole logical order (no competing order field)
    def test_inv5_no_competing_order_field(self):
        self._assert_invalid(lambda d: d["elements"][0].__setitem__("reading_order", 0))

    # 6. element/span IDs unique and references resolvable
    def test_inv6_duplicate_element_id(self):
        def mutate(d):
            d["elements"].append(copy.deepcopy(d["elements"][0]))
        self._assert_invalid(mutate)

    # 7. each Element has at least one primary span
    def test_inv7_no_primary_span(self):
        self._assert_invalid(lambda d: d["elements"][0]["source_spans"][0].__setitem__("role", "continuation"))

    # 8. bbox within page bounds
    def test_inv8_bbox_out_of_bounds(self):
        self._assert_invalid(lambda d: d["elements"][0]["source_spans"][0].__setitem__("bbox", [0.0, 0.0, 600.0, 50.0]))

    # 9. blank/failed pages do not contribute spans
    def test_inv9_blank_page_has_span(self):
        self._assert_invalid(lambda d: d["pages"][0].__setitem__("parse_status", "blank"))

    # 10. Element has no public text field
    def test_inv10_no_public_text(self):
        self._assert_invalid(lambda d: d["elements"][0].__setitem__("text", "x"))

    # 11. type matches content schema, unknown content fields rejected
    def test_inv11_text_content_unknown_field(self):
        self._assert_invalid(lambda d: d["elements"][0]["content"].__setitem__("normalized_text", "x"))

    # 12. TextContent.text non-empty
    def test_inv12_empty_text(self):
        self._assert_invalid(lambda d: d["elements"][0]["content"].__setitem__("text", ""))

    # 13. table grid covers all cells
    def test_inv13_cell_row_out_of_range(self):
        self._assert_invalid(lambda d: d["elements"][1]["content"]["cells"][0].__setitem__("row", 9))

    # 14. formula needs latex or recognized_formula
    def test_inv14_formula_without_recognition(self):
        def mutate(d):
            d["elements"][2]["content"]["latex"] = None
            d["elements"][2]["content"]["recognized_formula"] = None
        self._assert_invalid(mutate)

    # 15. figure asset completeness
    def test_inv15_asset_zero_pixels(self):
        self._assert_invalid(lambda d: d["elements"][3]["content"]["asset"].__setitem__("pixel_width", 0))

    # 16. provenance engine_id resolves
    def test_inv16_unknown_engine(self):
        self._assert_invalid(lambda d: d["elements"][0]["provenance"][0].__setitem__("engine_id", "ghost-1"))

    # 17. no QA/review/projection fields
    def test_inv17_no_qa_fields(self):
        self._assert_invalid(lambda d: d.__setitem__("publishable", True))


# =========================================================================== #
# Unknown-field rejection (multiple levels)
# =========================================================================== #

class UnknownFieldRejectionTest(unittest.TestCase):
    def _assert_unknown_rejected(self, mutate):
        doc = _valid()
        mutate(doc)
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_top_level(self):
        self._assert_unknown_rejected(lambda d: d.__setitem__("review_status", "pass"))

    def test_source(self):
        self._assert_unknown_rejected(lambda d: d["source"].__setitem__("extra", 1))

    def test_metadata(self):
        self._assert_unknown_rejected(lambda d: d["metadata"].__setitem__("tags", ["x"]))

    def test_element(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0].__setitem__("truth_level", "gold"))

    def test_span(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0]["source_spans"][0].__setitem__("confidence", 1.0))

    def test_table_cell(self):
        self._assert_unknown_rejected(lambda d: d["elements"][1]["content"]["cells"][0].__setitem__("bbox", [0, 0, 1, 1]))


# =========================================================================== #
# Span / engine ID resolvability
# =========================================================================== #

class IdResolvabilityTest(unittest.TestCase):
    def test_cell_references_unknown_span(self):
        doc = _valid()
        doc["elements"][1]["content"]["cells"][0]["source_span_ids"] = ["nope"]
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_provenance_references_unknown_engine(self):
        doc = _valid()
        doc["elements"][0]["provenance"][0]["engine_id"] = "ghost-1"
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_link_targets_unknown_element(self):
        doc = _valid()
        doc["elements"][0]["links"] = [{"type": "references", "target_element_id": "e999999"}]
        with self.assertRaises(SchemaError):
            validate_document(doc)


# =========================================================================== #
# Entry-point behaviour
# =========================================================================== #

class EntryPointTest(unittest.TestCase):
    def test_non_dict_rejected(self):
        with self.assertRaises(SchemaError):
            validate_document(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_compute_ids_returns_prefixed_forms(self):
        ids = compute_ids(_case_formula())
        self.assertTrue(ids["canonical_id"].startswith("canonical-sha256:"))
        self.assertTrue(ids["canonical_content_id"].startswith("canonical-content-sha256:"))
        self.assertTrue(ids["metadata_fingerprint"].startswith("metadata-sha256:"))
        for v in ids.values():
            self.assertEqual(len(v.split(":")[1]), 64)


if __name__ == "__main__":
    unittest.main()
