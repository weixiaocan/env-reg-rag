"""Unit tests for the V2 Chunker (plan 2.2 acceptance).

Uses a fake tokenizer (char-count) on a small synthetic V2 Canonical dict so
the tests run without loading the bge model, while still exercising every cut
rule: role filtering, heading flush, clause-start breakpoint, structured-unit
atomicity, short-clause merge, intra-clause recursive split, section_path
prefix, and chunk_id determinism.
"""

from __future__ import annotations

import unittest

from src.application.v2_chunker import chunk_document
from src.domain.v2_chunk import V2Chunk

SHA = "a" * 64


class _CharTokenizer:
    """Token count == char length (deterministic, model-free)."""

    def count(self, text: str) -> int:
        return len(text)


def _text_element(
    eid: str, role: str, text: str, *, label=None, section_path=None, page=1
) -> dict:
    return {
        "element_id": eid,
        "type": "text",
        "role": role,
        "label": label,
        "section_path": section_path or [],
        "content": {"text": text, "list": None},
        "source_spans": [
            {"span_id": f"s{eid}", "role": "primary", "physical_page": page,
             "bbox": [10.0, 10.0, 100.0, 50.0], "orientation_degrees": 0}
        ],
        "provenance": [{"operation": "extract", "method": "native_text",
                        "engine_id": "native-1", "confidence": 0.9}],
        "links": [],
    }


def _table_element(eid: str, *, page=1, section_path=None) -> dict:
    return {
        "element_id": eid, "type": "table", "role": "table", "label": "表1",
        "section_path": section_path or [],
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
        "source_spans": [{"span_id": f"s{eid}", "role": "primary", "physical_page": page,
                          "bbox": [10.0, 60.0, 200.0, 200.0], "orientation_degrees": 0}],
        "provenance": [{"operation": "recognize", "method": "table_recognition",
                        "engine_id": "native-1", "confidence": 0.8}],
        "links": [],
    }


def _formula_element(eid: str, *, page=1, section_path=None) -> dict:
    return {
        "element_id": eid, "type": "formula", "role": "display_formula", "label": "5.4.2-2",
        "section_path": section_path or [{"label": "5.4.2", "title": None}],
        "content": {"formula_number": "5.4.2-2", "latex": "Q=1",
                    "recognized_formula": None, "context_text": "式中：Q——流量。"},
        "source_spans": [{"span_id": f"s{eid}", "role": "primary", "physical_page": page,
                          "bbox": [10.0, 210.0, 100.0, 230.0], "orientation_degrees": 0}],
        "provenance": [{"operation": "recognize", "method": "formula_recognition",
                        "engine_id": "native-1", "confidence": 0.7}],
        "links": [],
    }


def _canonical(elements: list[dict], *, page_count=1) -> dict:
    return {
        "schema_version": "v2.canonical/1.0",
        "canonical_id": f"canonical-sha256:{SHA}",
        "canonical_content_id": f"canonical-content-sha256:{SHA}",
        "metadata_fingerprint": f"metadata-sha256:{SHA}",
        "document_id": f"pdf-sha256:{SHA}",
        "source": {"sha256": SHA, "file_name": "ex.pdf", "media_type": "application/pdf",
                   "page_count": page_count, "source_uri": None},
        "metadata": {
            "title": "示例", "alternate_titles": [], "language": "zh-CN",
            "identifiers": [{"scheme": "standard_number", "value": "T/CECS 758-2020"}],
            "document_kind": "standard", "jurisdictions": ["全国"], "issuing_authorities": [],
            "publication_date": None, "effective_from": None, "effective_to": None,
            "effective_status": "unknown", "effective_status_as_of": None,
            "registry_snapshot_id": f"source-registry-sha256:{SHA}",
        },
        "generation": {"run_id": "r1", "created_at": "2026-09-21T00:00:00Z",
                        "pipeline": "v2-pdf-pipeline", "pipeline_version": "0.1.0",
                        "config_sha256": SHA,
                        "engines": [{"engine_id": "native-1", "name": "pymupdf", "version": "1.23"}]},
        "pages": [{"physical_page": i, "printed_label": None, "width": 400.0, "height": 600.0,
                   "unit": "pt", "rotation_degrees": 0, "parse_status": "parsed",
                   "parse_error": None} for i in range(1, page_count + 1)],
        "elements": elements,
    }


def _chunk(canonical: dict) -> list[V2Chunk]:
    return chunk_document(canonical, tokenizer=_CharTokenizer(), token_budget=40, overlap=8)


class RoleFilterTest(unittest.TestCase):
    def test_page_number_header_footer_dropped(self):
        doc = _canonical([
            _text_element("e1", "page_number", "• 1 •"),
            _text_element("e2", "header", "页眉"),
            _text_element("e3", "footer", "页脚"),
            _text_element("e4", "watermark", "水印"),
            _text_element("e5", "unknown", "?"),
            _text_element("e6", "clause", "第5.3条 水质特征因子。"),
        ])
        chunks = _chunk(doc)
        ids = [eid for c in chunks for eid in c.element_ids]
        self.assertIn("e6", ids)
        for bad in ("e1", "e2", "e3", "e4", "e5"):
            self.assertNotIn(bad, ids)
        for c in chunks:
            self.assertNotIn("• 1 •", c.text)


class ClauseBreakpointTest(unittest.TestCase):
    def test_clause_start_flushes_previous(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 总则内容较长的一段说明文字。"),
            _text_element("e2", "clause", "第5.2条 另一条独立的条款说明文字。"),
        ])
        chunks = _chunk(doc)
        # two clauses -> two separate chunks (clause start flushes)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].element_ids, ["e1"])
        self.assertEqual(chunks[1].element_ids, ["e2"])

    def test_paragraph_with_clause_regex_flushes(self):
        # role=paragraph but text matches _CLAUSE_START -> treated as clause start
        doc = _canonical([
            _text_element("e1", "paragraph", "5.3.1 水质特征因子的选择应符合要求。"),
            _text_element("e2", "paragraph", "5.3.2 另一条编号条款的内容。"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(len(chunks), 2)


class HeadingFlushTest(unittest.TestCase):
    def test_heading_flushes_and_does_not_become_chunk(self):
        sp = [{"label": "5.3", "title": "水质"}]
        doc = _canonical([
            _text_element("e1", "clause", "第5.2条 前一条款内容文字。"),
            _text_element("e2", "heading", "5.3 水质", section_path=sp),
            _text_element("e3", "clause", "5.3.1 水质特征因子应能区分污水与雨水。", section_path=sp),
        ])
        chunks = _chunk(doc)
        ids = [eid for c in chunks for eid in c.element_ids]
        self.assertNotIn("e2", ids)  # heading text not emitted as a chunk


class StructuredAtomicTest(unittest.TestCase):
    def test_table_formula_atomic_single_element(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 总则内容说明文字。"),
            _table_element("e2"),
            _formula_element("e3"),
        ])
        chunks = _chunk(doc)
        struct = [c for c in chunks if c.metadata["element_types"] != ["text"]]
        self.assertEqual(len(struct), 2)
        for c in struct:
            self.assertEqual(len(c.element_ids), 1)  # atomic
        types = sorted(t for c in struct for t in c.metadata["element_types"])
        self.assertEqual(types, ["formula", "table"])


class ShortClauseMergeTest(unittest.TestCase):
    def test_adjacent_short_paragraphs_merge(self):
        sp = [{"label": "5.3", "title": "水质"}]
        doc = _canonical([
            _text_element("e1", "clause", "第5.3条 水质要求。", section_path=sp),
            _text_element("e2", "paragraph", "短句一。", section_path=sp),
            _text_element("e3", "paragraph", "短句二。", section_path=sp),
        ])
        chunks = _chunk(doc)
        merged = [c for c in chunks if len(c.element_ids) > 1]
        self.assertTrue(merged, "expected at least one merged chunk")
        # all three ids appear across chunks
        ids = {eid for c in chunks for eid in c.element_ids}
        self.assertEqual(ids, {"e1", "e2", "e3"})


class IntraClauseSplitTest(unittest.TestCase):
    def test_overlong_single_clause_split_and_under_cap(self):
        # one very long clause (char tokenizer, budget=40 -> many subs)
        long_text = "第5.3条 " + "水质特征因子应能区分。" * 20
        doc = _canonical([_text_element("e1", "clause", long_text)])
        chunks = _chunk(doc)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)  # char tokenizer == tokens


class StructuredOverlongSplitTest(unittest.TestCase):
    """A large table whose projected text exceeds the 512 hard cap must be
    split into multiple sub-chunks (each <= cap), all bound to the same
    element_id, with distinct chunk_ids. Regression for the emit_structured
    cap check (bge-small-zh max_length=512)."""

    def _big_table(self, eid="et", *, rows=60, section_path=None):
        cells = []
        for r in range(rows):
            cells.append({"row": r, "column": 0, "row_span": 1, "column_span": 1,
                          "role": "data", "text": f"指标参数第{r}项", "source_span_ids": []})
            cells.append({"row": r, "column": 1, "row_span": 1, "column_span": 1,
                          "role": "data", "text": f"数值范围说明{r}补充文字", "source_span_ids": []})
        return {
            "element_id": eid, "type": "table", "role": "table", "label": "表1",
            "section_path": section_path or [],
            "content": {"row_count": rows, "column_count": 2, "caption": "表1 示例大表",
                        "cells": cells, "notes": []},
            "source_spans": [{"span_id": f"s{eid}", "role": "primary", "physical_page": 1,
                              "bbox": [10.0, 60.0, 200.0, 200.0], "orientation_degrees": 0}],
            "provenance": [{"operation": "recognize", "method": "table_recognition",
                            "engine_id": "native-1", "confidence": 0.8}],
            "links": [],
        }

    def test_overlong_table_splits_under_cap_same_element_id(self):
        doc = _canonical([self._big_table()])
        # sanity: projected text is genuinely over the 512 hard cap
        from src.application.v2_chunker import _project_table
        proj = _project_table(doc["elements"][0]["content"])
        self.assertGreater(len(proj), 512)  # char tokenizer == tokens
        chunks = _chunk(doc)
        self.assertGreater(len(chunks), 1)
        # every sub-chunk respects the hard cap
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)
        # all bind the same element_id (trace chain intact)
        for c in chunks:
            self.assertEqual(c.element_ids, ["et"])
        # chunk_ids are distinct (text differs -> compute_chunk_id differs)
        self.assertEqual(len({c.chunk_id for c in chunks}), len(chunks))
        # all are typed table
        for c in chunks:
            self.assertEqual(c.metadata["element_types"], ["table"])

    def test_table_under_cap_stays_atomic(self):
        # a normal 2-row table fits the cap -> single atomic chunk (no split)
        doc = _canonical([_table_element("e2")])
        chunks = _chunk(doc)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].element_ids, ["e2"])


class SectionPathPrefixTest(unittest.TestCase):
    def test_prefix_built_from_element_section_path(self):
        sp = [{"label": "5", "title": "调查"}, {"label": "5.4", "title": "流量"}, {"label": "5.4.2", "title": None}]
        doc = _canonical([
            _text_element("e1", "clause", "5.4.2 管道流量测量方法说明文字。", section_path=sp),
        ])
        chunks = _chunk(doc)
        self.assertTrue(chunks)
        self.assertTrue(chunks[0].text.startswith("5 调查 > 5.4 流量 > 5.4.2\n"))


class ChunkIdDeterminismAndTraceTest(unittest.TestCase):
    def test_deterministic_and_unique(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 内容一。"),
            _text_element("e2", "clause", "第5.2条 内容二。"),
            _table_element("e3"),
        ])
        a = _chunk(doc)
        b = _chunk(doc)
        self.assertEqual([c.chunk_id for c in a], [c.chunk_id for c in b])
        ids = [c.chunk_id for c in a]
        self.assertEqual(len(ids), len(set(ids)))

    def test_element_ids_and_pages_traceable(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 内容一。", page=1),
            _text_element("e2", "clause", "第5.2条 内容二。", page=2),
        ], page_count=2)
        chunks = _chunk(doc)
        eids = {e["element_id"] for e in doc["elements"]}
        for c in chunks:
            for eid in c.element_ids:
                self.assertIn(eid, eids)
            for s in c.source_spans:
                self.assertIn(s.physical_page, {1, 2})

    def test_metadata_doc_level_fields(self):
        doc = _canonical([_text_element("e1", "clause", "第5.1条 内容。")])
        chunks = _chunk(doc)
        md = chunks[0].metadata
        self.assertEqual(md["standard_number"], "T/CECS 758-2020")
        self.assertEqual(md["document_kind"], "standard")
        self.assertEqual(md["jurisdictions"], ["全国"])
        self.assertEqual(md["element_types"], ["text"])


class QdrantPayloadCompatTest(unittest.TestCase):
    def test_payload_has_text_and_chunk_id_flat(self):
        doc = _canonical([_text_element("e1", "clause", "第5.1条 内容。")])
        chunks = _chunk(doc)
        payload = chunks[0].to_qdrant_payload()
        self.assertIn("chunk_id", payload)
        self.assertIn("text", payload)
        self.assertNotIn("metadata", payload)  # flattened
        self.assertEqual(payload["standard_number"], "T/CECS 758-2020")


if __name__ == "__main__":
    unittest.main()
