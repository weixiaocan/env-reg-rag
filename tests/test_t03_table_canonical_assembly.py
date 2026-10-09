"""T03 — table Canonical faithful assembly (pre-fix failing tests).

Asserts that every detected table region is consumed **faithfully** from the
upstream structured ``region["tables"]`` result (cells / spans / header roles /
caption / table number / unit / footnotes / attempts) -- never heuristically
reconstructed from overlapping plain-text bbox positions -- and that every
region produces exactly one reconcilable outcome (a real TableElement OR an
explicit failed/unsupported outcome), with no silent omission, no fake 1x1
empty cell, and no policy upgrade. T01/T02 contracts (default-deny,
strictest-wins, audit identity) must hold.

Pre-fix, the assertions below fail because:

* ``_build_table_element`` ignores ``region["tables"]`` and rebuilds cells from
  text-element bbox positions via ``_build_table_cells`` (T03 prohibition);
* an empty table fabricates a 1x1 empty cell to satisfy the schema (fake
  success);
* cross-page ``continues`` links are drawn from "most recent prior table with
  the same number" (order-dependent, not explicit evidence);
* failed / unsupported / no-cell regions silently produce a pseudo-table or are
  dropped with no outcome ledger;
* cell header roles / row_span / column_span / unit / footnotes from the
  structured result are discarded;
* consumed text source identities are not preserved on the table element.

Run::

    PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_t03_table_canonical_assembly.py -q
"""
from __future__ import annotations

import json
import os
import unittest

from src.application.canonical_assembly import assemble_document
from src.application.canonical_validation import compute_ids, validate_document
from src.application.evidence_policy import ANSWER, LOCATOR
from src.application.v2_chunker import chunk_document
from src.domain.canonical_document import CanonicalDocument, SchemaError

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DUMMY_SHA = "0" * 64
_PW, _PH = 595.0, 842.0

# A page-intermediate text element (the seam shape consumed by assembly).
def _txt(text, bbox):
    return {"text": text, "bbox": list(bbox)}


def _page(pno, *, route="native", decision="approved", text="", elements=None,
          regions=None, tables=None):
    if elements is None and text:
        elements = [_txt(text, [60.0, 100.0, 535.0, 140.0])]
    elif elements is None:
        elements = []
    return {
        "physical_page": pno, "page_index": pno - 1, "width": _PW, "height": _PH,
        "rotation": 0, "display_page_label": str(pno),
        "extraction_route": route, "decision_status": decision,
        "decision_reasons": [], "parser_name": "pymupdf-native" if route == "native" else "paddleocr-text",
        "parser_profile": "full-corpus-auto-v1", "text": text,
        "elements": elements, "regions": regions or [], "tables": tables or [],
    }


def _doc(pages, *, sha=_DUMMY_SHA):
    return {"sha256": sha, "file_name": "synthetic.pdf", "config_hash": "a" * 64,
            "processing_run_id": "run_t03", "metadata": {"document_kind": "standard"},
            "pages": pages}


def _table_region(pno, bbox, *, region_id=None, tables=None,
                  execution_status="completed", quality_status="needs_review",
                  issues=None, processing_config_id="unified-local-v5:cfg",
                  relations=None, selected_table_element_id=None):
    # Region ids are globally unique upstream (content fingerprint); the
    # default derives from the page + bbox so multi-region/multi-page
    # fixtures never collide and the ledger stays reconcilable.
    if region_id is None:
        region_id = f"up-tbl{pno:04d}{int(bbox[0]):04d}{int(bbox[1]):04d}"
    return {
        "region_id": region_id, "element_id": region_id, "kind": "table",
        "file_sha256": _DUMMY_SHA, "file_name": "synthetic.pdf",
        "document_version_id": "doc_synthetic", "physical_page": pno,
        "model_label": "table", "reading_order": 0,
        "execution_status": execution_status, "quality_status": quality_status,
        "issues": issues or [], "relations": relations or [],
        "processing_config_id": processing_config_id, "bbox": list(bbox),
        "coordinate_system": "page_points_top_left", "context": [], "text": "",
        "tables": tables if tables is not None else [],
        "selected_table_element_id": selected_table_element_id,
    }


def _structured_table(pno, bbox, *, cells, row_count=None, column_count=None,
                      caption="", table_number="", header_rows=None,
                      unit_context=None, footnotes=None,
                      is_explicit_continuation=False, html="",
                      review_status="pending_review", review_reasons=None,
                      element_id=None, processing_attempts=None):
    return {
        "element_id": element_id or f"crop-0-p{pno:04d}-table-0000",
        "physical_page": pno, "bbox": list(bbox),
        "coordinate_system": "page_points_top_left",
        "bbox_basis": "model_table_bbox", "cell_box_alignment": "model_order_pending_review",
        "html": html, "cells": cells,
        "row_count": row_count if row_count is not None else (max(c["row"] for c in cells) + 1 if cells else 0),
        "column_count": column_count if column_count is not None else (max(c["col"] for c in cells) + 1 if cells else 0),
        "caption": caption, "caption_candidates": [caption] if caption else [],
        "header_rows": header_rows or [],
        "unit_context": unit_context or [], "footnotes": footnotes or [],
        "missing_context": [], "review_status": review_status,
        "review_reasons": review_reasons or ["table_transcription_requires_review"],
        "publishable": False, "table_number": table_number,
        "is_explicit_continuation": is_explicit_continuation,
        "region_quality": {"status": review_status, "can_link_source_page": True,
                           "can_use_transcription": False, "can_use_for_calculation": False,
                           "reasons": review_reasons or []},
        "processing_attempts": processing_attempts or [],
    }


def _cell(row, col, text, *, row_span=1, col_span=1, role="data", bbox=None):
    c = {"row": row, "col": col, "row_span": row_span, "col_span": col_span,
         "cell_role": role, "text": text}
    if bbox is not None:
        c["bbox"] = list(bbox)
    return c


def _assemble(page_doc, *, formula_pages=None):
    doc = assemble_document(page_doc, formula_pages=formula_pages or {})
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
    return doc


def _tables(doc):
    return [e for e in doc["elements"] if e["type"] == "table"]


class _Tok:
    def count(self, text): return len(text)
    def encode(self, text): return text.encode("utf-8")


def _chunks(doc):
    return chunk_document(doc, token_budget=480, overlap=0, tokenizer=_Tok())


# =========================================================================== #
# 1. Content matrix -- faithful structured consumption
# =========================================================================== #
class TestTableContentMatrix(unittest.TestCase):
    def _simple(self):
        # 2x2 header + data, exact coordinates and roles from the model.
        cells = [_cell(0, 0, "指标", role="header"), _cell(0, 1, "限值", role="header"),
                 _cell(1, 0, "磷酸盐", role="data"), _cell(1, 1, "≥8.0 mg/L", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2, header_rows=[0])
        return _table_region(1, [60, 100, 300, 200], tables=[tbl])

    def test_simple_table_cells_faithful(self):
        region = self._simple()
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        ts = _tables(doc)
        self.assertEqual(len(ts), 1)
        c = ts[0]["content"]
        self.assertEqual(c["row_count"], 2)
        self.assertEqual(c["column_count"], 2)
        self.assertEqual(len(c["cells"]), 4)
        # Cell coordinates preserved exactly (not heuristically rebuilt).
        coords = {(cell["row"], cell["column"]) for cell in c["cells"]}
        self.assertEqual(coords, {(0, 0), (0, 1), (1, 0), (1, 1)})
        # Header role preserved (not degraded to data).
        roles = {(cell["row"], cell["column"]): cell["role"] for cell in c["cells"]}
        self.assertEqual(roles[(0, 0)], "header")
        self.assertEqual(roles[(0, 1)], "header")
        self.assertEqual(roles[(1, 0)], "data")
        # Cell text faithful.
        texts = {(cell["row"], cell["column"]): cell["text"] for cell in c["cells"]}
        self.assertEqual(texts[(1, 1)], "≥8.0 mg/L")

    def test_multilevel_header_roles_preserved(self):
        # Two header rows; header role must not collapse to data.
        cells = [_cell(0, 0, "分类", role="header", col_span=2),
                 _cell(1, 0, "名称", role="header"), _cell(1, 1, "代码", role="header"),
                 _cell(2, 0, "A类", role="data"), _cell(2, 1, "01", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 250], cells=cells,
                                row_count=3, column_count=2, header_rows=[0, 1])
        region = _table_region(1, [60, 100, 300, 250], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        c = _tables(doc)[0]["content"]
        roles = {(cell["row"], cell["column"]): cell["role"] for cell in c["cells"]}
        self.assertEqual(roles[(0, 0)], "header")
        self.assertEqual(roles[(1, 0)], "header")
        self.assertEqual(roles[(2, 0)], "data")
        # Merged header span preserved.
        top = next(cell for cell in c["cells"] if cell["row"] == 0 and cell["column"] == 0)
        self.assertEqual(top["column_span"], 2)

    def test_merged_cells_span_preserved(self):
        cells = [_cell(0, 0, "指标", role="header"), _cell(0, 1, "范围", role="header"),
                 _cell(1, 0, "降雨", role="data"),
                 _cell(1, 1, "1～3年一遇", role="data", row_span=3)]
        tbl = _structured_table(1, [60, 100, 300, 250], cells=cells,
                                row_count=4, column_count=2)
        region = _table_region(1, [60, 100, 300, 250], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        c = _tables(doc)[0]["content"]
        merged = next(cell for cell in c["cells"] if cell["row"] == 1 and cell["column"] == 1)
        self.assertEqual(merged["row_span"], 3)
        self.assertEqual(merged["column_span"], 1)

    def test_span_exceeds_grid_hard_fails(self):
        cells = [_cell(0, 0, "x", role="data", row_span=5)]  # grid is 1x1
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=1, column_count=1)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        with self.assertRaises(SchemaError):
            validate_document(_assemble(_doc([_page(1, regions=[region])])))

    def test_empty_cell_text_kept_not_dropped(self):
        # A legitimately empty data cell must keep its coordinate.
        cells = [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                 _cell(1, 0, "row1", role="data"), _cell(1, 1, "", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        c = _tables(doc)[0]["content"]
        empty = next(cell for cell in c["cells"] if cell["row"] == 1 and cell["column"] == 1)
        self.assertEqual(empty["text"], "")
        self.assertEqual(len(c["cells"]), 4)


# =========================================================================== #
# 2. caption / table number / unit / footnotes
# =========================================================================== #
class TestTableCaptionUnitFootnotes(unittest.TestCase):
    def _base_cells(self):
        return [_cell(0, 0, "项目", role="header"), _cell(0, 1, "数值", role="header"),
                _cell(1, 0, "总量", role="data"), _cell(1, 1, "100", role="data")]

    def test_caption_and_table_number_preserved(self):
        tbl = _structured_table(1, [60, 100, 300, 200], cells=self._base_cells(),
                                row_count=2, column_count=2,
                                caption="表3.2.3 内涝防治指标", table_number="3.2.3")
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        t = _tables(doc)[0]
        self.assertEqual(t["content"]["caption"], "表3.2.3 内涝防治指标")
        # table number / label preserved.
        self.assertEqual(t["content"].get("table_number"), "3.2.3")

    def test_caption_missing_not_guessed(self):
        tbl = _structured_table(1, [60, 100, 300, 200], cells=self._base_cells(),
                                row_count=2, column_count=2, caption="")
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        t = _tables(doc)[0]
        self.assertIsNone(t["content"]["caption"])

    def test_unit_context_preserved_and_traceable(self):
        uc = [{"text": "mg/L", "physical_page": 1, "bbox": [60, 90, 90, 100],
               "source_cell": {"row": 0, "col": 1}, "association_status": "pending_review",
               "basis": "literal_cell_text"}]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=self._base_cells(),
                                row_count=2, column_count=2, unit_context=uc)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        c = _tables(doc)[0]["content"]
        uc_out = c.get("unit_context") or []
        self.assertTrue(any(u.get("text") == "mg/L" for u in uc_out),
                        "unit context must be preserved on the table content")

    def test_footnotes_preserved_and_bound(self):
        fn = [{"text": "注：数据为设计值。", "physical_page": 1, "bbox": [60, 210, 300, 220]}]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=self._base_cells(),
                                row_count=2, column_count=2, footnotes=fn)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        c = _tables(doc)[0]["content"]
        notes = c.get("notes") or []
        self.assertTrue(any("设计值" in n.get("text", "") for n in notes),
                        "footnotes must be preserved as table notes")


# =========================================================================== #
# 3. source spans / region identity
# =========================================================================== #
class TestTableSourceSpans(unittest.TestCase):
    def test_table_element_has_authoritative_region_identity(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                 _cell(1, 0, "1", role="data"), _cell(1, 1, "2", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2)
        region = _table_region(1, [60, 100, 300, 200], region_id="up-auth1234567890abcd",
                               tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        t = _tables(doc)[0]
        ids = t.get("source_region_ids") or []
        self.assertIn("up-auth1234567890abcd", ids,
                      "TableElement must carry its authoritative source region id")
        self.assertEqual(len(ids), 1, "exactly one authoritative region per table element")

    def test_table_region_span_present(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=1)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        t = _tables(doc)[0]
        # Primary span on the table region bbox.
        spans = [s for s in t["source_spans"] if s["role"] == "primary"]
        self.assertTrue(spans)
        self.assertEqual(spans[0]["physical_page"], 1)


# =========================================================================== #
# 4. status / fallback / failure matrix
# =========================================================================== #
class TestStatusFallbackMatrix(unittest.TestCase):
    def _cells(self):
        return [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                _cell(1, 0, "1", role="data"), _cell(1, 1, "2", role="data")]

    def test_cropped_model_completed_legal_cells(self):
        tbl = _structured_table(1, [60, 100, 300, 200], cells=self._cells(),
                                row_count=2, column_count=2,
                                element_id="crop-0-p0001-table-0000")
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(len(_tables(doc)), 1)
        # Default needs_review even on cropped-model success.
        self.assertEqual(_tables(doc)[0].get("quality_status"), "needs_review")
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in _chunks(doc)))

    def test_cropped_failed_full_page_fallback_completed(self):
        fb = _structured_table(1, [60, 100, 300, 200], cells=self._cells(),
                               row_count=2, column_count=2,
                               element_id="p0001-table-0000",
                               processing_attempts=[
                                   {"route": "cropped_table", "execution_status": "failed",
                                    "reason": "table_region_structure_not_recovered"},
                                   {"route": "full_page_fallback", "execution_status": "completed"}])
        region = _table_region(1, [60, 100, 300, 200], tables=[fb])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(len(_tables(doc)), 1)
        self.assertEqual(_tables(doc)[0].get("quality_status"), "needs_review")
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in _chunks(doc)))
        # The FAILED cropped attempt must not let the result pose as
        # cropped_table: the route comes from the COMPLETED attempt only.
        out = doc["structured_outcomes"][0]
        sel = next(a for a in out["attempts"] if a["selected"])
        self.assertEqual(sel["route"], "full_page_fallback")
        self.assertEqual(sel["execution_status"], "completed")
        self.assertEqual(sel["result_id"], "p0001-table-0000")
        self.assertEqual(out["selected_result_id"], "p0001-table-0000")
        crop = next(a for a in out["attempts"] if a["route"] == "cropped_table")
        self.assertEqual(crop["execution_status"], "failed")
        self.assertFalse(crop["selected"])

    def test_all_attempts_failed_no_element_but_outcome(self):
        # All recorded attempts failed (cropped_table then full_page_fallback
        # both failed, no legal result) -> must NOT fabricate a table; the
        # outcome carries the FULL failed attempt ledger with no selection.
        tbl = _structured_table(
            1, [60, 100, 300, 200], cells=[],
            processing_attempts=[
                {"attempt_id": "att-crop", "route": "cropped_table",
                 "execution_status": "failed", "reason": "timeout"},
                {"attempt_id": "att-full", "route": "full_page_fallback",
                 "execution_status": "failed", "reason": "no_structure"}])
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl],
                               execution_status="failed",
                               issues=["no_matching_structured_table"])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [], "failed region must not produce a fake table")
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 1, "failed region must produce one outcome")
        self.assertEqual(outs[0]["execution_status"], "failed")
        self.assertIsNone(outs[0].get("element_id"))
        # Full attempt ledger preserved, nothing selected.
        atts = outs[0]["attempts"]
        self.assertEqual([a["attempt_id"] for a in atts], ["att-crop", "att-full"])
        self.assertTrue(all(a["execution_status"] == "failed" for a in atts))
        self.assertTrue(all(not a["selected"] for a in atts))
        self.assertIsNone(outs[0]["selected_attempt_id"])
        self.assertEqual(atts[0]["failure_reason"], "timeout")

    def test_tables_present_but_cells_empty_outcome(self):
        tbl = _structured_table(1, [60, 100, 300, 200], cells=[],
                                row_count=0, column_count=0)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [], "empty cells must not yield a fake table")
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 1)
        # A detected region with results but no usable cells is a FAILURE:
        # never completed, never not_detected (region exists => detected).
        self.assertIn(outs[0]["execution_status"], {"failed", "unsupported"})
        self.assertNotIn("completed", outs[0]["execution_status"])
        self.assertNotEqual(outs[0]["execution_status"], "not_detected")
        self.assertIsNone(outs[0]["element_id"])
        self.assertIn("table_cells_not_recovered", outs[0]["issues"])

    def test_detected_region_tables_empty_outcome(self):
        region = _table_region(1, [60, 100, 300, 200], tables=[])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [])
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 1)
        # not_detected can only mean "no region detected" -- impossible for an
        # outcome of an EXISTING region.
        self.assertEqual(outs[0]["execution_status"], "failed")
        self.assertIn("no_structured_table_result", outs[0]["issues"])
        self.assertIsNone(outs[0]["element_id"])

    def test_execution_status_missing_safe(self):
        region = _table_region(1, [60, 100, 300, 200], tables=[], execution_status="")
        # Empty/missing execution_status -> still an outcome, conservative.
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["execution_status"], "failed")
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in _chunks(doc)))

    def test_invalid_bbox_region_unsupported_outcome(self):
        # A detected table region with invalid coordinates produces no slot
        # but MUST still close with exactly one unsupported outcome.
        region = _table_region(1, [60, 100, 300, 200], tables=[])
        region["bbox"] = None
        region["issues"] = ["invalid_detection_coordinates"]
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["execution_status"], "unsupported")
        self.assertIsNone(outs[0]["element_id"])


# =========================================================================== #
# 5. cross-page continuation -- explicit evidence only
# =========================================================================== #
class TestCrossPageContinuation(unittest.TestCase):
    def _cells(self, off=0):
        return [_cell(0, 0, f"r{off}", role="data"), _cell(0, 1, f"v{off}", role="data")]

    def test_explicit_continuation_builds_link(self):
        # Page 1: table 3.1; page 2: explicit continuation with a relation
        # targeting page 1's region.
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="3.1")
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-tab1AAAAAAAAAAAA",
                           tables=[t1])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.1",
                               is_explicit_continuation=True)
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-tab2BBBBBBBBBBBB",
                           tables=[t2],
                           relations=[{"kind": "explicit_continued_table_number",
                                       "target_region_id": "up-tab1AAAAAAAAAAAA",
                                       "target_physical_page": 1, "verified": False,
                                       "basis": "literal_continuation_caption_same_number"}])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        validate_document(doc)
        ts = _tables(doc)
        self.assertEqual(len(ts), 2)
        # The continuation (page 2) must carry a continues link to page 1's element.
        cont = next(t for t in ts if any(s["physical_page"] == 2 for s in t["source_spans"]))
        links = [l for l in cont["links"] if l["type"] == "continues"]
        self.assertTrue(links, "explicit continuation must build a continues link")
        # Target resolves to the page-1 table element.
        target_ids = {t["element_id"] for t in ts}
        self.assertIn(links[0]["target_element_id"], target_ids)

    def test_adjacent_page_different_number_no_link(self):
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="3.1")
        r1 = _table_region(1, [60, 100, 300, 200], tables=[t1])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.2")
        r2 = _table_region(2, [60, 100, 300, 200], tables=[t2])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        for t in _tables(doc):
            self.assertEqual([l for l in t["links"] if l["type"] == "continues"], [],
                             "different table numbers must not auto-link")

    def test_forward_reference_resolves(self):
        # The continuation appears on page 1 and its TARGET on page 2 (forward
        # reference). Phase-2 resolution must still build the link.
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="3.1",
                               is_explicit_continuation=True)
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-tabFWD00000000001",
                           tables=[t1],
                           relations=[{"kind": "explicit_continued_table_number",
                                       "target_region_id": "up-tabFWD00000000002",
                                       "target_physical_page": 2, "verified": False,
                                       "basis": "literal_continuation"}])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.1")
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-tabFWD00000000002",
                           tables=[t2])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        validate_document(doc)
        ts = _tables(doc)
        cont = next(t for t in ts if any(s["physical_page"] == 1 for s in t["source_spans"]))
        links = [l for l in cont["links"] if l["type"] == "continues"]
        self.assertTrue(links, "forward reference must resolve (phase 2)")
        target = next(t for t in ts if t["element_id"] == links[0]["target_element_id"])
        self.assertTrue(any(s["physical_page"] == 2 for s in target["source_spans"]))
        # No unresolved issue on either side.
        self.assertFalse([i for i in cont["quality_issues"]
                          if i.startswith("continuation_")])

    def test_relation_target_missing_unresolved(self):
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.1",
                               is_explicit_continuation=True)
        r2 = _table_region(2, [60, 100, 300, 200], tables=[t2],
                           relations=[{"kind": "explicit_continued_table_number",
                                       "target_region_id": "up-NOSUCH000000000000",
                                       "target_physical_page": 1, "verified": False,
                                       "basis": "literal_continuation"}])
        # Page 1 exists (source page of the missing target) but carries no table.
        doc = _assemble(_doc([_page(1), _page(2, regions=[r2])]))
        validate_document(doc)
        ts = _tables(doc)
        # No dangling link to a non-existent element.
        for t in ts:
            self.assertEqual([l for l in t["links"] if l["type"] == "continues"], [])
        # The unresolved target is an EXPLICIT issue on the element AND its
        # outcome -- never a silent drop.
        cont = next(t for t in ts
                    if any(s["physical_page"] == 2 for s in t["source_spans"]))
        self.assertTrue(any(i.startswith("continuation_target_missing:")
                            for i in cont["quality_issues"]))
        outs = doc.get("structured_outcomes") or []
        target_out = next(o for o in outs if o.get("element_id") == cont["element_id"])
        self.assertTrue(any(i.startswith("continuation_target_missing:")
                            for i in target_out["issues"]))

    def test_relation_conflict_no_arbitrary_link(self):
        # Two explicit continuation relations resolving to DIFFERENT prior
        # tables -> conflicting evidence: no link is created and the conflict
        # is recorded on the element and outcome.
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="3.1")
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-tabCFL00000000001",
                           tables=[t1])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.2")
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-tabCFL00000000002",
                           tables=[t2])
        t3 = _structured_table(3, [60, 100, 300, 200], cells=self._cells(2),
                               row_count=1, column_count=2, table_number="3.3",
                               is_explicit_continuation=True)
        r3 = _table_region(3, [60, 100, 300, 200], region_id="up-tabCFL00000000003",
                           tables=[t3],
                           relations=[
                               {"kind": "explicit_continued_table_number",
                                "target_region_id": "up-tabCFL00000000001",
                                "target_physical_page": 1, "verified": False,
                                "basis": "caption_same_number"},
                               {"kind": "explicit_continued_table_number",
                                "target_region_id": "up-tabCFL00000000002",
                                "target_physical_page": 2, "verified": False,
                                "basis": "layout_continuation"}])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2]),
                              _page(3, regions=[r3])]))
        validate_document(doc)
        ts = _tables(doc)
        cont = next(t for t in ts if any(s["physical_page"] == 3 for s in t["source_spans"]))
        self.assertEqual([l for l in cont["links"] if l["type"] == "continues"], [],
                         "conflicting targets must not produce an arbitrary link")
        self.assertTrue(any(i.startswith("continuation_relation_conflict:")
                            for i in cont["quality_issues"]))
        outs = doc.get("structured_outcomes") or []
        target_out = next(o for o in outs if o.get("element_id") == cont["element_id"])
        self.assertTrue(any(i.startswith("continuation_relation_conflict:")
                            for i in target_out["issues"]))

    def test_continuation_different_table_numbers_no_link(self):
        # Explicit relation + resolvable target, but the recovered table
        # numbers differ (3.1 vs 3.2): the numbering is contradictory, so no
        # chain is built and the conflict is explicit.
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="3.1")
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-numAAAAAAAAAAAA",
                           tables=[t1])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.2",
                               is_explicit_continuation=True)
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-numBBBBBBBBBBBB",
                           tables=[t2],
                           relations=[{"kind": "explicit_continued_table_number",
                                       "target_region_id": "up-numAAAAAAAAAAAA",
                                       "target_physical_page": 1, "verified": False,
                                       "basis": "literal_continuation"}])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        validate_document(doc)
        cont = next(t for t in _tables(doc)
                    if any(s["physical_page"] == 2 for s in t["source_spans"]))
        self.assertEqual([l for l in cont["links"] if l["type"] == "continues"], [],
                         "conflicting table numbers must not build a chain")
        self.assertTrue(any(i.startswith("continuation_table_number_conflict:")
                            for i in cont["quality_issues"]))
        outs = doc.get("structured_outcomes") or []
        out = next(o for o in outs if o.get("element_id") == cont["element_id"])
        self.assertTrue(any(i.startswith("continuation_table_number_conflict:")
                            for i in out["issues"]))

    def test_continuation_missing_table_number_no_link(self):
        # Target table number was never recovered: numbering evidence is
        # incomplete -- no chain, explicit missing-number issue.
        t1 = _structured_table(1, [60, 100, 300, 200], cells=self._cells(0),
                               row_count=1, column_count=2, table_number="")
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-misAAAAAAAAAAAA",
                           tables=[t1])
        t2 = _structured_table(2, [60, 100, 300, 200], cells=self._cells(1),
                               row_count=1, column_count=2, table_number="3.1",
                               is_explicit_continuation=True)
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-misBBBBBBBBBBBB",
                           tables=[t2],
                           relations=[{"kind": "explicit_continued_table_number",
                                       "target_region_id": "up-misAAAAAAAAAAAA",
                                       "target_physical_page": 1, "verified": False,
                                       "basis": "literal_continuation"}])
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        validate_document(doc)
        cont = next(t for t in _tables(doc)
                    if any(s["physical_page"] == 2 for s in t["source_spans"]))
        self.assertEqual([l for l in cont["links"] if l["type"] == "continues"], [])
        self.assertIn("continuation_table_number_missing", cont["quality_issues"])
        outs = doc.get("structured_outcomes") or []
        out = next(o for o in outs if o.get("element_id") == cont["element_id"])
        self.assertIn("continuation_table_number_missing", out["issues"])


# =========================================================================== #
# 6. text / table dedup -- preserve source identity
# =========================================================================== #
class TestTextTableDedup(unittest.TestCase):
    def test_overlapping_text_consumed_not_duplicated(self):
        # A text element fully inside the table region must NOT also be emitted
        # as a standalone TextElement (no duplicate answer chunk), but its
        # source identity (upstream element id, page, bbox) must be preserved
        # on the consuming table element (dedup is traceable, not a drop).
        cells = [_cell(0, 0, "指标", role="header"), _cell(0, 1, "限值", role="header"),
                 _cell(1, 0, "磷酸盐", role="data"), _cell(1, 1, "≥8.0", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        # Text element overlapping the table region (with an upstream id).
        inside = {"element_id": "p0000-b0042", "type": "text",
                  "text": "磷酸盐 ≥8.0", "bbox": [70, 110, 290, 190]}
        doc = _assemble(_doc([_page(1, text="", elements=[inside], regions=[region])]))
        text_els = [e for e in doc["elements"] if e["type"] == "text"
                    and "磷酸盐" in e["content"]["text"]]
        self.assertEqual(text_els, [], "overlapping text must not be duplicated as a text element")
        # Table still present and carries the consumed source identity.
        self.assertEqual(len(_tables(doc)), 1)
        consumed = _tables(doc)[0].get("consumed_sources") or []
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["element_id"], "p0000-b0042")
        self.assertEqual(consumed[0]["physical_page"], 1)
        self.assertEqual(consumed[0]["bbox"], [70.0, 110.0, 290.0, 190.0])

    def test_partial_overlap_text_not_whole_deleted(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(1, [60, 100, 200, 200], cells=cells, row_count=2, column_count=1)
        region = _table_region(1, [60, 100, 200, 200], tables=[tbl])
        # A text element that only partially overlaps (extends well outside).
        adjacent = _txt("本标准适用于城镇内涝防治工程。", [210, 100, 535, 140])
        doc = _assemble(_doc([_page(1, text="", elements=[adjacent], regions=[region])]))
        text_els = [e for e in doc["elements"] if e["type"] == "text"
                    and "内涝防治" in e["content"]["text"]]
        self.assertTrue(text_els, "partially-overlapping text must not be deleted wholesale")


# =========================================================================== #
# 7. policy + audit identity
# =========================================================================== #
class TestTablePolicyAndIdentity(unittest.TestCase):
    def _doc_with_table(self, **tbl_kw):
        cells = [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                 _cell(1, 0, "1", role="data"), _cell(1, 1, "2", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2, **tbl_kw)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        return _assemble(_doc([_page(1, regions=[region])]))

    def test_table_default_locator_only(self):
        doc = self._doc_with_table()
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in _chunks(doc)))

    def test_table_content_change_flips_both_ids(self):
        d1 = self._doc_with_table()
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["content"]["cells"][0]["text"] = "CHANGED"
        ids2 = compute_ids(d2)
        self.assertNotEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_table_quality_change_flips_canonical_id_only(self):
        d1 = self._doc_with_table()
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["quality_status"] = "quarantined"
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_execution_status_change_flips_canonical_id(self):
        d1 = self._doc_with_table()
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["execution_status"] = "failed"
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_legacy_artifact_still_validates(self):
        p = os.path.join(_REPO_ROOT, "data", "canonical", "v2", "corpus-37456321a968",
                         "f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941.canonical.json")
        if not os.path.isfile(p):
            self.skipTest("published artifact not present")
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_document(doc)  # must not raise
        ids = compute_ids(doc)
        self.assertEqual(doc["canonical_id"], ids["canonical_id"],
                         "legacy artifact canonical_id unchanged")


# =========================================================================== #
# 8. integrity invariants
# =========================================================================== #
class TestIntegrityInvariants(unittest.TestCase):
    def test_detected_regions_equal_outcomes(self):
        r1 = _table_region(1, [60, 100, 300, 200], tables=[
            _structured_table(1, [60, 100, 300, 200], cells=[
                _cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")],
                row_count=2, column_count=1)])
        r2 = _table_region(2, [60, 100, 300, 200], tables=[])  # failed
        doc = _assemble(_doc([_page(1, regions=[r1]), _page(2, regions=[r2])]))
        validate_document(doc)
        outs = doc.get("structured_outcomes") or []
        self.assertEqual(len(outs), 2, "one outcome per detected region")
        successful = [o for o in outs if o.get("element_id")]
        self.assertEqual(len(successful), len(_tables(doc)),
                         "successful outcomes == TableElement count")

    def test_each_cell_in_grid(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=1)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)  # validate enforces grid bounds

    def test_input_order_invariant(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                 _cell(1, 0, "1", role="data"), _cell(1, 1, "2", role="data")]
        tbl_a = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                  row_count=2, column_count=2)
        # Same cells, reversed input order.
        tbl_b = _structured_table(1, [60, 100, 300, 200], cells=list(reversed(cells)),
                                  row_count=2, column_count=2)
        d_a = _assemble(_doc([_page(1, regions=[_table_region(1, [60, 100, 300, 200], tables=[tbl_a])])]))
        d_b = _assemble(_doc([_page(1, regions=[_table_region(1, [60, 100, 300, 200], tables=[tbl_b])])]))
        # Content identity stable regardless of input cell order.
        self.assertEqual(d_a["canonical_content_id"], d_b["canonical_content_id"])

    def test_region_list_order_swap_stable(self):
        # Swapping the region list on a page must not change element identity,
        # outcome identity or the ledger.
        def _mk(order):
            cells = [_cell(0, 0, "X", role="header"), _cell(1, 0, "1", role="data")]
            r1 = _table_region(1, [60, 100, 200, 200], region_id="up-ordAAAAAAAAAAAA",
                               tables=[_structured_table(
                                   1, [60, 100, 200, 200], cells=cells,
                                   row_count=2, column_count=1)])
            r2 = _table_region(1, [300, 100, 440, 200], region_id="up-ordBBBBBBBBBBBB",
                               tables=[_structured_table(
                                   1, [300, 100, 440, 200], cells=cells,
                                   row_count=2, column_count=1)])
            return _assemble(_doc([_page(1, regions=[r1, r2] if order else [r2, r1])]))
        d1, d2 = _mk(True), _mk(False)
        self.assertEqual(d1["canonical_id"], d2["canonical_id"])
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])
        self.assertEqual(
            sorted(o["region_id"] for o in d1["structured_outcomes"]),
            sorted(o["region_id"] for o in d2["structured_outcomes"]))
        for o1, o2 in zip(
                sorted(d1["structured_outcomes"], key=lambda o: o["region_id"]),
                sorted(d2["structured_outcomes"], key=lambda o: o["region_id"])):
            self.assertEqual(o1["attempts"], o2["attempts"])
            self.assertEqual(o1["selected_attempt_id"], o2["selected_attempt_id"])


# =========================================================================== #
# 9. attempt ledger -- full, closed, exactly-one-selected (REJECT fix #1)
# =========================================================================== #
class TestAttemptLedger(unittest.TestCase):
    def _attempts(self):
        return [
            {"attempt_id": "att-crop-0001", "route": "cropped_table",
             "execution_status": "failed",
             "reason": "table_region_structure_not_recovered",
             "engine_name": "local-table-v5", "engine_version": "5.1.0",
             "processing_config_id": "unified-local-v5:cfg", "config_hash": "c" * 64,
             "input_sha256": "i" * 64, "output_sha256": None,
             "confidence": 0.42},
            {"attempt_id": "att-full-0002", "route": "full_page_fallback",
             "execution_status": "completed",
             "engine_name": "local-table-fullpage", "engine_version": "5.1.0",
             "processing_config_id": "unified-local-v5:cfg", "config_hash": "c" * 64,
             "input_sha256": "i" * 64, "output_sha256": "o" * 64,
             "confidence": 0.91},
        ]

    def _doc_with_attempts(self, attempts):
        cells = [_cell(0, 0, "A", role="header"), _cell(0, 1, "B", role="header"),
                 _cell(1, 0, "1", role="data"), _cell(1, 1, "2", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=2,
                                processing_attempts=attempts)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        return _assemble(_doc([_page(1, regions=[region])]))

    def test_attempts_saved_field_by_field(self):
        doc = self._doc_with_attempts(self._attempts())
        validate_document(doc)
        out = (doc.get("structured_outcomes") or [])[0]
        atts = out["attempts"]
        self.assertEqual(len(atts), 2, "every recorded attempt is preserved")
        by_id = {a["attempt_id"]: a for a in atts}
        crop, full = by_id["att-crop-0001"], by_id["att-full-0002"]
        # Full field-level preservation: route / status / engine / config /
        # identity / confidence / failure reason.
        self.assertEqual(crop["route"], "cropped_table")
        self.assertEqual(crop["execution_status"], "failed")
        self.assertEqual(crop["engine_name"], "local-table-v5")
        self.assertEqual(crop["engine_version"], "5.1.0")
        self.assertEqual(crop["config_id"], "unified-local-v5:cfg")
        self.assertEqual(crop["config_hash"], "c" * 64)
        self.assertEqual(crop["input_identity"], "i" * 64)
        self.assertEqual(crop["failure_reason"],
                         "table_region_structure_not_recovered")
        self.assertEqual(crop["confidence"], 0.42)
        self.assertEqual(full["output_identity"], "o" * 64)
        self.assertEqual(full["confidence"], 0.91)
        # Exactly one selected attempt, referenced by its stable id.
        self.assertEqual(sum(1 for a in atts if a["selected"]), 1)
        self.assertTrue(full["selected"])
        self.assertFalse(crop["selected"])
        self.assertEqual(out["selected_attempt_id"], "att-full-0002")
        # Single admissible result -> selection is trivially unambiguous.
        self.assertEqual(out["selection_basis"], "single_admissible")

    def test_attempt_order_swap_same_identity(self):
        # Swapping the attempts array must not change any identity.
        d1 = self._doc_with_attempts(self._attempts())
        d2 = self._doc_with_attempts(list(reversed(self._attempts())))
        self.assertEqual(d1["canonical_id"], d2["canonical_id"])
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])

    def test_no_attempts_synthesises_single_selected(self):
        # Repo semantics: legal cells + no attempt records == successful
        # primary cropped recognition -> exactly one selected attempt.
        doc = self._doc_with_attempts([])
        validate_document(doc)
        out = (doc.get("structured_outcomes") or [])[0]
        atts = out["attempts"]
        self.assertEqual(len(atts), 1)
        self.assertTrue(atts[0]["selected"])
        self.assertEqual(atts[0]["route"], "cropped_table")
        self.assertEqual(out["selected_attempt_id"], atts[0]["attempt_id"])
        self.assertEqual(out["selection_basis"], "single_admissible")


# =========================================================================== #
# 10. deterministic, order-independent selection (REJECT fix #3)
# =========================================================================== #
class TestDeterministicSelection(unittest.TestCase):
    def _cells(self, tag):
        return [_cell(0, 0, f"{tag}h", role="header"),
                _cell(1, 0, f"{tag}d", role="data")]

    def _region(self, tables, **kw):
        return _table_region(1, [60, 100, 300, 200], tables=tables, **kw)

    def test_geometry_unique_max_selection(self):
        # Two admissible results, same route: the one whose bbox is contained
        # in the region wins on unique maximal overlap -- regardless of order.
        a = _structured_table(1, [60, 100, 300, 200], cells=self._cells("a"),
                              row_count=2, column_count=1,
                              element_id="up-cropAAAAAAAAAAAA")
        b = _structured_table(1, [200, 100, 300, 200], cells=self._cells("b"),
                              row_count=2, column_count=1,
                              element_id="up-cropBBBBBBBBBBBB")
        d1 = _assemble(_doc([_page(1, regions=[self._region([a, b])])]))
        d2 = _assemble(_doc([_page(1, regions=[self._region([b, a])])]))
        for d in (d1, d2):
            validate_document(d)
        out = d1["structured_outcomes"][0]
        self.assertEqual(out["selection_basis"], "max_region_overlap")
        self.assertEqual(out["admissible_result_ids"],
                         sorted(["up-cropAAAAAAAAAAAA", "up-cropBBBBBBBBBBBB"]))
        # Same winner under order swap: content identity identical.
        self.assertEqual(d1["canonical_id"], d2["canonical_id"])
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])
        # Winner identity: result id, attempt id and route are asserted
        # explicitly -- not just the selection basis (REJECT issue 3).
        self.assertEqual(out["selected_result_id"], "up-cropAAAAAAAAAAAA")
        sel = next(a for a in out["attempts"] if a["selected"])
        self.assertEqual(sel["result_id"], "up-cropAAAAAAAAAAAA")
        self.assertEqual(out["selected_attempt_id"], sel["attempt_id"])
        self.assertEqual(sel["route"], "cropped_table")
        self.assertEqual(sel["execution_status"], "completed")
        winner_cells = d1["elements"][0]["content"]["cells"]
        self.assertEqual(winner_cells[0]["text"], "ah")
        # Winner content identical under order swap.
        self.assertEqual(d2["elements"][0]["content"]["cells"],
                         d1["elements"][0]["content"]["cells"])

    def test_equal_overlap_prefers_unique_smallest_enclosing_crop(self):
        # Real T/CECS 758 p42 shape: two model results both fully cover the
        # detected region, so raw intersection area ties.  The tighter crop is
        # independent geometry evidence and must win deterministically rather
        # than turning a recoverable region into an execution failure.
        tight = _structured_table(
            1, [58, 98, 302, 202], cells=self._cells("tight"),
            row_count=2, column_count=1,
            element_id="up-tightAAAAAAAAAAA")
        broad = _structured_table(
            1, [20, 60, 340, 240], cells=self._cells("broad"),
            row_count=2, column_count=1,
            element_id="up-broadBBBBBBBBBBB")
        region = self._region([broad, tight])
        d1 = _assemble(_doc([_page(1, regions=[region])]))
        region_swapped = self._region([tight, broad])
        d2 = _assemble(_doc([_page(1, regions=[region_swapped])]))
        for doc in (d1, d2):
            validate_document(doc)
            out = doc["structured_outcomes"][0]
            self.assertEqual(out["selection_basis"], "min_enclosing_excess")
            self.assertEqual(out["selected_result_id"], "up-tightAAAAAAAAAAA")
            self.assertEqual(doc["elements"][0]["content"]["cells"][0]["text"],
                             "tighth")
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])

    def test_ambiguous_tie_no_arbitrary_element(self):
        # Two admissible results with IDENTICAL geometry (and no other
        # evidence) -> no sufficient selection basis: needs_review failure
        # outcome, NO arbitrary TableElement.
        a = _structured_table(1, [60, 100, 300, 200], cells=self._cells("a"),
                              row_count=2, column_count=1,
                              element_id="up-tieAAAAAAAAAAAAA")
        b = _structured_table(1, [60, 100, 300, 200], cells=self._cells("b"),
                              row_count=2, column_count=1,
                              element_id="up-tieBBBBBBBBBBBBB")
        doc = _assemble(_doc([_page(1, regions=[self._region([a, b])])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [],
                         "ambiguous selection must not emit an arbitrary table")
        out = doc["structured_outcomes"][0]
        # D1 separation: an ambiguous selection is an EXECUTION failure with
        # a needs_review QUALITY verdict -- never execution_status
        # "needs_review" (which would conflate processing with review).
        self.assertEqual(out["execution_status"], "failed")
        self.assertEqual(out["quality_status"], "needs_review")
        self.assertIsNone(out["element_id"])
        self.assertIsNone(out["selected_result_id"])
        self.assertIsNone(out["selected_attempt_id"])
        self.assertTrue(out["attempts"])
        self.assertTrue(all(not a["selected"] for a in out["attempts"]))
        self.assertIn("ambiguous_structured_table_selection", out["issues"])
        # Order swap -> the same semantic outcome (both candidates recorded).
        d2 = _assemble(_doc([_page(1, regions=[self._region([b, a])])]))
        self.assertEqual(d2["structured_outcomes"][0]["admissible_result_ids"],
                         out["admissible_result_ids"])
        self.assertEqual(d2["structured_outcomes"][0]["execution_status"],
                         out["execution_status"])

    def test_route_priority_beats_fallback(self):
        # cropped-table fallback completed vs ocr-geometry fallback completed:
        # the repo-defined priority picks full_page_fallback over
        # ocr_geometry_fallback, independent of array order.
        a = _structured_table(
            1, [60, 100, 300, 200], cells=self._cells("a"), row_count=2,
            column_count=1, element_id="up-rtAAAAAAAAAAAAAA",
            processing_attempts=[
                {"route": "cropped_table", "execution_status": "failed"},
                {"route": "full_page_fallback", "execution_status": "completed"}])
        b = _structured_table(
            1, [60, 100, 300, 200], cells=self._cells("b"), row_count=2,
            column_count=1, element_id="up-rtBBBBBBBBBBBBBB",
            processing_attempts=[
                {"route": "ocr_geometry_fallback", "execution_status": "completed"}])
        d1 = _assemble(_doc([_page(1, regions=[self._region([a, b])])]))
        d2 = _assemble(_doc([_page(1, regions=[self._region([b, a])])]))
        for d in (d1, d2):
            validate_document(d)
        out = d1["structured_outcomes"][0]
        self.assertEqual(out["selection_basis"], "route_priority")
        self.assertEqual(d1["canonical_id"], d2["canonical_id"])
        winner = d1["elements"][0]["content"]["cells"][0]["text"]
        self.assertEqual(winner, "ah")
        # Winner identity: result id + attempt id + ACTUAL route asserted.
        self.assertEqual(out["selected_result_id"], "up-rtAAAAAAAAAAAAAA")
        sel = next(a for a in out["attempts"] if a["selected"])
        self.assertEqual(sel["result_id"], "up-rtAAAAAAAAAAAAAA")
        self.assertEqual(out["selected_attempt_id"], sel["attempt_id"])
        # The selected attempt's route is the COMPLETED fallback route, not
        # the failed cropped route it also carries in the ledger.
        self.assertEqual(sel["route"], "full_page_fallback")
        self.assertEqual(sel["execution_status"], "completed")
        both = {a["route"] for a in out["attempts"]}
        self.assertIn("cropped_table", both)
        self.assertEqual(d2["structured_outcomes"][0]["selected_attempt_id"],
                         out["selected_attempt_id"])

    def test_explicit_selection_marker(self):
        a = _structured_table(1, [60, 100, 300, 200], cells=self._cells("a"),
                              row_count=2, column_count=1,
                              element_id="up-exAAAAAAAAAAAAAA")
        b = _structured_table(1, [200, 100, 300, 200], cells=self._cells("b"),
                              row_count=2, column_count=1,
                              element_id="up-exBBBBBBBBBBBBBB")
        doc = _assemble(_doc([_page(1, regions=[self._region(
            [a, b], selected_table_element_id="up-exBBBBBBBBBBBBBB")])]))
        validate_document(doc)
        out = doc["structured_outcomes"][0]
        self.assertEqual(out["selection_basis"], "explicit_selection")
        self.assertEqual(doc["elements"][0]["content"]["cells"][0]["text"], "bh")

    def test_selected_id_not_found_fails_loudly(self):
        a = _structured_table(1, [60, 100, 300, 200], cells=self._cells("a"),
                              row_count=2, column_count=1,
                              element_id="up-nfAAAAAAAAAAAAAA")
        b = _structured_table(1, [200, 100, 300, 200], cells=self._cells("b"),
                              row_count=2, column_count=1,
                              element_id="up-nfBBBBBBBBBBBBBB")
        doc = _assemble(_doc([_page(1, regions=[self._region(
            [a, b], selected_table_element_id="up-NOSUCH0000000000")])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [])
        out = doc["structured_outcomes"][0]
        self.assertEqual(out["execution_status"], "failed")
        self.assertIn("selected_table_not_found", out["issues"])
        self.assertIsNone(out["element_id"])


# =========================================================================== #
# 11. outcome validation invariants + audit identity (REJECT requirement 8)
# =========================================================================== #
class TestOutcomeValidationAndIdentity(unittest.TestCase):
    def _valid_doc(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=1)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        return _assemble(_doc([_page(1, regions=[region])]))

    def _structural_error(self, mutate, doc=None):
        """Mutate the valid doc and return the STRUCTURAL SchemaError message.

        Structural assertions must go through ``CanonicalDocument.from_dict``
        directly (NOT the digest-verifying ``validate_document``), so a test
        can never pass via an unrelated digest mismatch after mutating a
        fixture -- it must trigger the intended invariant and prove it by
        asserting the specific error cause.
        """
        doc = json.loads(json.dumps(doc if doc is not None else self._valid_doc()))
        mutate(doc)
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        return str(ctx.exception)

    def test_illegal_status_not_detected_rejected(self):
        msg = self._structural_error(lambda d: d["structured_outcomes"][0].update(
            execution_status="not_detected"))
        self.assertIn("illegal execution_status", msg)
        self.assertIn("not_detected", msg)

    def test_illegal_outcome_exec_status_needs_review_rejected(self):
        # D1 status separation: ``needs_review`` is a QUALITY verdict, never
        # an execution status. An ambiguous table selection must close as
        # execution_status="failed" + quality_status="needs_review" instead.
        msg = self._structural_error(lambda d: d["structured_outcomes"][0].update(
            execution_status="needs_review"))
        self.assertIn("illegal execution_status", msg)
        self.assertIn("needs_review", msg)

    def test_ambiguous_selection_uses_failed_exec_with_needs_review_quality(self):
        # Real assembly path: the ambiguous p42-style tie produces an outcome
        # whose quality verdict carries the review need, not its exec status.
        a = _structured_table(1, [60, 100, 300, 200], cells=[
            _cell(0, 0, "a", role="data")], row_count=1, column_count=1,
            element_id="up-d1AAAAAAAAAAAAAA")
        b = _structured_table(1, [60, 100, 300, 200], cells=[
            _cell(0, 0, "b", role="data")], row_count=1, column_count=1,
            element_id="up-d1BBBBBBBBBBBBBB")
        region = _table_region(1, [60, 100, 300, 200], tables=[a, b])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [])
        out = doc["structured_outcomes"][0]
        self.assertEqual(out["execution_status"], "failed")
        self.assertEqual(out["quality_status"], "needs_review")
        self.assertIsNone(out["element_id"])
        self.assertIsNone(out["selected_result_id"])
        self.assertIsNone(out["selected_attempt_id"])
        self.assertIn("ambiguous_structured_table_selection", out["issues"])
        # Ledger preserved: both candidates' attempts, nothing selected.
        self.assertEqual(len(out["attempts"]), 2)
        self.assertTrue(all(not a["selected"] for a in out["attempts"]))

    def test_illegal_kind_rejected(self):
        # T05 extended the closed kind vocabulary with "figure" (kind-scoped
        # routes); a kind outside table|formula|figure stays illegal.
        msg = self._structural_error(
            lambda d: d["structured_outcomes"][0].update(kind="plot"))
        self.assertIn("kind", msg)

    def test_illegal_attempt_route_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["attempts"][0]["route"] = "teleport_route"
        msg = self._structural_error(mutate)
        self.assertIn("illegal route", msg)

    def test_illegal_attempt_status_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["attempts"][0]["execution_status"] = "skipped"
        msg = self._structural_error(mutate)
        self.assertIn("illegal execution_status", msg)

    def test_attempt_missing_result_binding_rejected(self):
        # Every attempt must be bound to its candidate result identity.
        def mutate(d):
            d["structured_outcomes"][0]["attempts"][0]["result_id"] = None
            d["structured_outcomes"][0]["selected_result_id"] = None
        msg = self._structural_error(mutate)
        self.assertIn("result_id", msg)

    def test_illegal_confidence_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["attempts"][0]["confidence"] = 7.5
        msg = self._structural_error(mutate)
        self.assertIn("confidence", msg)

    def test_illegal_bbox_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["bbox"] = [10, 10]  # not 4 numbers
        msg = self._structural_error(mutate)
        self.assertIn("bbox", msg)

    def test_illegal_issues_type_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["issues"] = "table_cells_not_recovered"
        msg = self._structural_error(mutate)
        self.assertIn("issues", msg)

    def test_illegal_admissible_ids_type_rejected(self):
        def mutate(d):
            d["structured_outcomes"][0]["admissible_result_ids"] = "crop-0"
        msg = self._structural_error(mutate)
        self.assertIn("admissible_result_ids", msg)

    def test_completed_without_element_rejected(self):
        msg = self._structural_error(
            lambda d: d["structured_outcomes"][0].update(element_id=None))
        self.assertIn("completed requires an element_id", msg)

    def test_failure_with_element_rejected(self):
        msg = self._structural_error(lambda d: d["structured_outcomes"][0].update(
            execution_status="failed"))
        self.assertIn("must not reference an element", msg)

    def test_completed_with_two_selected_attempts_rejected(self):
        def mutate(d):
            o = d["structured_outcomes"][0]
            extra = dict(o["attempts"][0])
            extra["attempt_id"] = "att-extra-9999"
            o["attempts"].append(extra)  # second selected attempt
        msg = self._structural_error(mutate)
        self.assertIn("exactly one selected attempt", msg)

    def test_completed_without_selection_basis_rejected(self):
        msg = self._structural_error(
            lambda d: d["structured_outcomes"][0].update(selection_basis=None))
        self.assertIn("selection_basis", msg)

    def test_selected_result_id_not_admissible_rejected(self):
        msg = self._structural_error(lambda d: d["structured_outcomes"][0].update(
            selected_result_id="up-NOTADMISSIBLE00000"))
        self.assertIn("not among admissible_result_ids", msg)

    def test_selected_attempt_not_completed_rejected(self):
        # The selected attempt itself must be COMPLETED: selecting a failed
        # attempt must not back a completed outcome.
        def mutate(d):
            d["structured_outcomes"][0]["attempts"][0]["execution_status"] = "failed"
        msg = self._structural_error(mutate)
        self.assertIn("execution_status 'completed'", msg)

    def test_selected_attempt_result_mismatch_rejected(self):
        # Two candidates; the selected attempt belongs to the selected result.
        # Repointing selected_result_id at the OTHER result is an inconsistency
        # that must be rejected by structure, not by digest.
        a = _structured_table(1, [60, 100, 300, 200], cells=[
            _cell(0, 0, "a", role="data")], row_count=1, column_count=1,
            element_id="up-misAAAAAAAAAAAAAA")
        b = _structured_table(1, [60, 100, 290, 200], cells=[
            _cell(0, 0, "b", role="data")], row_count=1, column_count=1,
            element_id="up-misBBBBBBBBBBBBBB")
        region = _table_region(1, [60, 100, 300, 200], tables=[a, b])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        out = doc["structured_outcomes"][0]
        self.assertEqual(out["selected_result_id"], "up-misAAAAAAAAAAAAAA")

        def mutate(d):
            d["structured_outcomes"][0]["selected_result_id"] = "up-misBBBBBBBBBBBBBB"
        msg = self._structural_error(mutate, doc=doc)
        self.assertIn("does not match selected_result_id", msg)

    def test_duplicate_region_outcome_rejected(self):
        def mutate(d):
            d["structured_outcomes"].append(
                json.loads(json.dumps(d["structured_outcomes"][0])))
        msg = self._structural_error(mutate)
        self.assertIn("duplicate structured outcome", msg)

    def test_missing_outcome_for_table_element_rejected(self):
        # Dropping the only outcome leaves a table element unclaimed: the
        # ledger gate fires (new artifact, audit fields present).
        msg = self._structural_error(
            lambda d: d["structured_outcomes"].pop())
        self.assertIn("structured-outcomes ledger", msg)

    def test_partial_reconciliation_rejected(self):
        # A second table element with NO outcome for it: the ledger is present
        # but out of reconciliation with the element list.
        def mutate(d):
            clone = json.loads(json.dumps(d["elements"][0]))
            clone["element_id"] = "e000042"
            clone["source_spans"][0]["span_id"] = "sX"
            d["elements"].append(clone)
        msg = self._structural_error(mutate)
        self.assertIn("out of reconciliation", msg)

    def test_element_claimed_by_two_completed_outcomes_rejected(self):
        def mutate(d):
            clone = json.loads(json.dumps(d["structured_outcomes"][0]))
            clone["region_id"] = "up-CLONE000000000009"
            d["structured_outcomes"].append(clone)
        msg = self._structural_error(mutate)
        self.assertIn("claimed by more than one completed structured outcome", msg)

    def test_attempt_change_flips_canonical_id_only(self):
        d1 = self._valid_doc()
        d2 = json.loads(json.dumps(d1))
        d2["structured_outcomes"][0]["attempts"][0]["confidence"] = 0.5
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_outcome_issue_change_flips_canonical_id(self):
        d1 = self._valid_doc()
        d2 = json.loads(json.dumps(d1))
        d2["structured_outcomes"][0]["issues"].append("extra_audit_issue")
        ids2 = compute_ids(d2)
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_consumed_sources_change_flips_canonical_id_only(self):
        d1 = self._valid_doc()
        d1["elements"][0]["consumed_sources"] = [{
            "element_id": "p0000-b0007", "physical_page": 1,
            "bbox": [70.0, 110.0, 280.0, 190.0]}]
        ids1 = compute_ids(d1)
        d1["canonical_id"] = ids1["canonical_id"]
        d1["canonical_content_id"] = ids1["canonical_content_id"]
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["consumed_sources"] = []
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_relation_change_flips_canonical_id(self):
        # Two identical builds except one page-2 region carries an explicit
        # continuation relation: the relation (and the link / unresolved-issue
        # it produces) must change the audit identity.
        cells_a = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        cells_b = [_cell(0, 0, "B", role="header"), _cell(1, 0, "2", role="data")]
        r1 = _table_region(1, [60, 100, 300, 200], region_id="up-relAAAAAAAAAAAA",
                           tables=[_structured_table(
                               1, [60, 100, 300, 200], cells=cells_a,
                               row_count=2, column_count=1,
                               table_number="9.1")])
        r2 = _table_region(2, [60, 100, 300, 200], region_id="up-relBBBBBBBBBBBB",
                           tables=[_structured_table(
                               2, [60, 100, 300, 200], cells=cells_b,
                               row_count=2, column_count=1,
                               table_number="9.1")])
        base = [_page(1, regions=[r1]), _page(2, regions=[r2])]
        d1 = _assemble(_doc(base))
        r2_rel = _table_region(2, [60, 100, 300, 200], region_id="up-relBBBBBBBBBBBB",
                               tables=[_structured_table(
                                   2, [60, 100, 300, 200], cells=cells_b,
                                   row_count=2, column_count=1,
                                   table_number="9.1")],
                               relations=[{"kind": "explicit_continued_table_number",
                                           "target_region_id": "up-relAAAAAAAAAAAA",
                                           "target_physical_page": 1,
                                           "verified": False,
                                           "basis": "literal_continuation"}])
        d2 = _assemble(_doc([_page(1, regions=[r1]),
                             _page(2, regions=[r2_rel])]))
        self.assertNotEqual(d1["canonical_id"], d2["canonical_id"])
        # The matching numbers let the chain build: no number issue either way.
        rel_el = next(e for e in d2["elements"] if e["type"] == "table"
                      and any(s["physical_page"] == 2 for s in e["source_spans"]))
        self.assertFalse([i for i in rel_el["quality_issues"]
                          if i.startswith("continuation_table_number")])


# =========================================================================== #
# 12. full candidate ledger + success semantics (REJECT issue 2)
# =========================================================================== #
class TestFullCandidateLedger(unittest.TestCase):
    def test_non_empty_cells_all_attempts_failed_no_completed_element(self):
        # Cells are present but EVERY recorded attempt failed: contradictory
        # evidence must NOT produce a completed TableElement -- the outcome
        # closes as an explicit failure carrying the full unselected ledger.
        tbl = _structured_table(
            1, [60, 100, 300, 200],
            cells=[_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")],
            row_count=2, column_count=1, element_id="up-failAAAAAAAAAAAA",
            processing_attempts=[
                {"route": "cropped_table", "execution_status": "failed",
                 "reason": "structure_not_recovered"},
                {"route": "full_page_fallback", "execution_status": "failed",
                 "reason": "low_confidence"}])
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        self.assertEqual(_tables(doc), [])
        out = doc["structured_outcomes"][0]
        self.assertEqual(out["execution_status"], "failed")
        self.assertIsNone(out["element_id"])
        self.assertIsNone(out["selected_result_id"])
        self.assertIsNone(out["selected_attempt_id"])
        self.assertIn("selected_result_has_no_completed_attempt", out["issues"])
        self.assertEqual(len(out["attempts"]), 2)
        self.assertTrue(all(a["execution_status"] == "failed" for a in out["attempts"]))
        self.assertTrue(all(not a["selected"] for a in out["attempts"]))
        self.assertEqual(out["admissible_result_ids"], ["up-failAAAAAAAAAAAA"])

    def test_ledger_covers_every_candidate_bound_to_its_result(self):
        # Two candidates, each with its own attempts: the outcome ledger must
        # contain ALL of them, each bound to its result id.
        a = _structured_table(
            1, [60, 100, 300, 200],
            cells=[_cell(0, 0, "a", role="data")], row_count=1, column_count=1,
            element_id="up-ledAAAAAAAAAAAAAA",
            processing_attempts=[
                {"attempt_id": "att-a-crop", "route": "cropped_table",
                 "execution_status": "failed", "reason": "timeout"}])
        b = _structured_table(
            1, [60, 100, 290, 200],
            cells=[_cell(0, 0, "b", role="data")], row_count=1, column_count=1,
            element_id="up-ledBBBBBBBBBBBBBB",
            processing_attempts=[
                {"attempt_id": "att-b-full", "route": "full_page_fallback",
                 "execution_status": "completed"}])
        region = _table_region(1, [60, 100, 300, 200], tables=[a, b])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        out = doc["structured_outcomes"][0]
        atts = out["attempts"]
        self.assertEqual(len(atts), 2, "all candidates' attempts are saved")
        by_id = {x["attempt_id"]: x for x in atts}
        self.assertEqual(by_id["att-a-crop"]["result_id"], "up-ledAAAAAAAAAAAAAA")
        self.assertEqual(by_id["att-a-crop"]["execution_status"], "failed")
        self.assertEqual(by_id["att-b-full"]["result_id"], "up-ledBBBBBBBBBBBBBB")
        self.assertEqual(by_id["att-b-full"]["execution_status"], "completed")
        # The completed attempt of the route-priority winner is selected;
        # nothing else.
        self.assertEqual(out["selection_basis"], "route_priority")
        self.assertEqual(out["selected_result_id"], "up-ledBBBBBBBBBBBBBB")
        self.assertEqual(out["selected_attempt_id"], "att-b-full")
        self.assertEqual(by_id["att-b-full"]["selected"], True)
        self.assertEqual(by_id["att-a-crop"]["selected"], False)

    def test_ledger_order_independent(self):
        a = _structured_table(
            1, [60, 100, 300, 200],
            cells=[_cell(0, 0, "a", role="data")], row_count=1, column_count=1,
            element_id="up-lorAAAAAAAAAAAAAA",
            processing_attempts=[
                {"attempt_id": "att-a-crop", "route": "cropped_table",
                 "execution_status": "failed", "reason": "timeout"}])
        b = _structured_table(
            1, [60, 100, 290, 200],
            cells=[_cell(0, 0, "b", role="data")], row_count=1, column_count=1,
            element_id="up-lorBBBBBBBBBBBBBB",
            processing_attempts=[
                {"attempt_id": "att-b-full", "route": "full_page_fallback",
                 "execution_status": "completed"}])
        d1 = _assemble(_doc([_page(1, regions=[_table_region(
            1, [60, 100, 300, 200], tables=[a, b])])]))
        d2 = _assemble(_doc([_page(1, regions=[_table_region(
            1, [60, 100, 300, 200], tables=[b, a])])]))
        self.assertEqual(d1["canonical_id"], d2["canonical_id"])
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])
        o1, o2 = d1["structured_outcomes"][0], d2["structured_outcomes"][0]
        self.assertEqual(o1["attempts"], o2["attempts"])
        self.assertEqual(o1["selected_attempt_id"], o2["selected_attempt_id"])
        self.assertEqual(o1["selected_result_id"], o2["selected_result_id"])
        self.assertEqual(d1["elements"][0]["content"],
                         d2["elements"][0]["content"])

    def test_region_without_results_single_region_level_attempt(self):
        region = _table_region(1, [60, 100, 300, 200], tables=[])
        doc = _assemble(_doc([_page(1, regions=[region])]))
        validate_document(doc)
        out = doc["structured_outcomes"][0]
        self.assertEqual(len(out["attempts"]), 1)
        att = out["attempts"][0]
        self.assertEqual(att["execution_status"], "failed")
        self.assertIsNone(att["result_id"])
        self.assertEqual(att["failure_reason"], "no_structured_table_result")
        self.assertFalse(att["selected"])


# =========================================================================== #
# 13. ledger gate (no bypass via "legacy compat") + to_dict round-trip
# =========================================================================== #
class TestOutcomeGateAndRoundTrip(unittest.TestCase):
    def _valid_doc(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(1, [60, 100, 300, 200], cells=cells,
                                row_count=2, column_count=1)
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        return _assemble(_doc([_page(1, regions=[region])]))

    def _legacy_doc(self):
        """A pre-T02 shaped document: a table element, NO outcome ledger, and
        NO provenance/quality audit fields anywhere."""
        return {
            "schema_version": "v2.canonical/1.0",
            "canonical_id": "canonical-sha256:" + "b" * 64,
            "canonical_content_id": "canonical-content-sha256:" + "b" * 64,
            "metadata_fingerprint": "metadata-sha256:" + "b" * 64,
            "document_id": "pdf-sha256:" + "0" * 64,
            "source": {"sha256": "0" * 64, "file_name": "legacy.pdf",
                       "media_type": "application/pdf", "page_count": 1,
                       "source_uri": None},
            "metadata": {
                "title": "Legacy doc", "alternate_titles": [], "language": "zh-CN",
                "identifiers": [], "document_kind": "standard",
                "jurisdictions": [], "issuing_authorities": [],
                "publication_date": None, "effective_from": None,
                "effective_to": None, "effective_status": "unknown",
                "effective_status_as_of": None,
                "registry_snapshot_id": "source-registry-sha256:unknown",
            },
            "generation": {
                "run_id": "run-legacy", "created_at": "2026-01-01T00:00:00Z",
                "pipeline": "v2-pdf-pipeline", "pipeline_version": "1.0",
                "config_sha256": "c" * 64,
                "engines": [{"engine_id": "ocr-1", "name": "ocr",
                             "version": "1"}],
            },
            "pages": [{"physical_page": 1, "printed_label": None,
                       "width": 595.0, "height": 842.0, "unit": "pt",
                       "rotation_degrees": 0, "parse_status": "parsed",
                       "parse_error": None}],
            "elements": [{
                "element_id": "e000001", "type": "table", "role": "table",
                "label": None, "section_path": [],
                "content": {
                    "row_count": 1, "column_count": 1, "caption": None,
                    "cells": [{"row": 0, "column": 0, "row_span": 1,
                               "column_span": 1, "role": "data", "text": "x",
                               "source_span_ids": ["s1"]}],
                    "notes": []},
                "source_spans": [{"span_id": "s1", "role": "primary",
                                  "physical_page": 1,
                                  "bbox": [10.0, 10.0, 100.0, 60.0],
                                  "orientation_degrees": 0}],
                "provenance": [{"operation": "recognize",
                                "method": "table_recognition",
                                "engine_id": "ocr-1", "confidence": None}],
                "links": [],
            }],
        }

    def test_new_artifact_with_table_but_no_outcome_ledger_rejected(self):
        doc = self._valid_doc()
        doc["structured_outcomes"] = []
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(doc)
        self.assertIn("structured-outcomes ledger", str(ctx.exception))
        self.assertIn("legacy", str(ctx.exception))

    def test_new_artifact_with_outcomes_removed_via_to_dict_rejected(self):
        # Round-tripping through a dict that LOST the ledger (old to_dict
        # behaviour) can never re-enter as a valid new artifact.
        doc = self._valid_doc()
        stripped = json.loads(json.dumps(doc))
        stripped.pop("structured_outcomes")
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(stripped)
        self.assertIn("structured-outcomes ledger", str(ctx.exception))

    def test_legacy_artifact_with_table_but_no_outcomes_still_validates(self):
        legacy = self._legacy_doc()
        CanonicalDocument.from_dict(legacy)  # must not raise
        # ...but the SAME document carrying a single T02 audit field is a new
        # artifact and must satisfy the gate.
        newer = json.loads(json.dumps(legacy))
        newer["elements"][0]["quality_status"] = "needs_review"
        with self.assertRaises(SchemaError) as ctx:
            CanonicalDocument.from_dict(newer)
        self.assertIn("structured-outcomes ledger", str(ctx.exception))

    def test_to_dict_round_trip_is_lossless(self):
        # to_dict used to drop structured_outcomes entirely (REJECT issue 1).
        d1 = self._valid_doc()
        d2 = CanonicalDocument.from_dict(d1).to_dict()
        self.assertIn("structured_outcomes", d2)
        self.assertEqual(d2["structured_outcomes"], d1["structured_outcomes"])
        # Round-trip through the serialised form is stable (from_dict
        # normalises the representation -- e.g. absent optional cell fields
        # become explicit nulls -- and a second trip changes nothing).
        d3 = CanonicalDocument.from_dict(d2).to_dict()
        self.assertEqual(d3, d2)
        validate_document(d2)

    def test_to_dict_round_trip_preserves_full_attempt_ledger(self):
        cells = [_cell(0, 0, "A", role="header"), _cell(1, 0, "1", role="data")]
        tbl = _structured_table(
            1, [60, 100, 300, 200], cells=cells, row_count=2, column_count=1,
            processing_attempts=[
                {"attempt_id": "att-rt-crop", "route": "cropped_table",
                 "execution_status": "failed", "reason": "timeout"},
                {"attempt_id": "att-rt-full", "route": "full_page_fallback",
                 "execution_status": "completed"}])
        region = _table_region(1, [60, 100, 300, 200], tables=[tbl])
        d1 = _assemble(_doc([_page(1, regions=[region])]))
        d2 = CanonicalDocument.from_dict(d1).to_dict()
        o1, o2 = d1["structured_outcomes"][0], d2["structured_outcomes"][0]
        self.assertEqual(o2["attempts"], o1["attempts"])
        self.assertEqual(o2["selected_attempt_id"], o1["selected_attempt_id"])
        self.assertEqual(o2["selected_result_id"], o1["selected_result_id"])


if __name__ == "__main__":
    unittest.main()
