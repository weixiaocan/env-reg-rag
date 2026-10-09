"""T01 — evidence admission policy projection tests.

Verifies the single-direction projection from element-level quality/usage to
chunk usage_policy (strictest source wins), structured-default locator-only,
quarantine/needs_review/missing never answer, enrich never upgrades policy,
and the retriever missing-policy safe default. These tests are written BEFORE
the fix and are expected to fail on the pre-fix code (hardcoded
``answer_and_citation`` doc-level policy, enrich unconditional overwrite,
retriever answer default).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]

SHA = "b" * 64


# --------------------------------------------------------------------------- #
# Fixtures (mirror tests/test_v2_chunker.py shape)
# --------------------------------------------------------------------------- #

def _text_element(
    eid: str, role: str, text: str, *, section_path=None, page=1,
    quality_status=None, usage_policy=None, policy_reasons=None,
) -> dict:
    el = {
        "element_id": eid,
        "type": "text",
        "role": role,
        "label": None,
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
    if quality_status is not None:
        el["quality_status"] = quality_status
    if usage_policy is not None:
        el["usage_policy"] = usage_policy
    if policy_reasons is not None:
        el["policy_reasons"] = policy_reasons
    return el


def _table_element(eid: str, *, page=1, quality_status=None, usage_policy=None,
                   review_assertion_id=None) -> dict:
    el = {
        "element_id": eid, "type": "table", "role": "table", "label": "表1",
        "section_path": [],
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
    if quality_status is not None:
        el["quality_status"] = quality_status
    if usage_policy is not None:
        el["usage_policy"] = usage_policy
    if review_assertion_id is not None:
        el["review_assertion_id"] = review_assertion_id
    return el


def _formula_element(eid: str, *, page=1, usage_policy=None) -> dict:
    el = {
        "element_id": eid, "type": "formula", "role": "display_formula", "label": "5.4.2-2",
        "section_path": [{"label": "5.4.2", "title": None}],
        "content": {"formula_number": "5.4.2-2", "latex": "Q=1",
                    "recognized_formula": None, "context_text": "式中：Q——流量。"},
        "source_spans": [{"span_id": f"s{eid}", "role": "primary", "physical_page": page,
                          "bbox": [10.0, 210.0, 100.0, 230.0], "orientation_degrees": 0}],
        "provenance": [{"operation": "recognize", "method": "formula_recognition",
                        "engine_id": "native-1", "confidence": 0.7}],
        "links": [],
    }
    if usage_policy is not None:
        el["usage_policy"] = usage_policy
    return el


def _figure_element(eid: str, *, page=1) -> dict:
    return {
        "element_id": eid, "type": "figure", "role": "figure", "label": "图1",
        "section_path": [],
        "content": {
            "caption": "图1 示例", "references": [],
            "asset": {"ref": "assets/x.png", "sha256": "c" * 64, "media_type": "image/png",
                      "pixel_width": 10, "pixel_height": 10},
        },
        "source_spans": [{"span_id": f"s{eid}", "role": "primary", "physical_page": page,
                          "bbox": [10.0, 250.0, 100.0, 300.0], "orientation_degrees": 0}],
        "provenance": [{"operation": "crop", "method": "image_extraction",
                        "engine_id": "native-1", "confidence": 0.6}],
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


class _CharTokenizer:
    def count(self, text: str) -> int:
        return len(text)


def _chunk(canonical: dict):
    from src.application.v2_chunker import chunk_document
    return chunk_document(canonical, tokenizer=_CharTokenizer(), token_budget=40, overlap=8)


# --------------------------------------------------------------------------- #
# Chunker projection
# --------------------------------------------------------------------------- #

class StructuredDefaultLocatorTest(unittest.TestCase):
    """D3: table/formula/figure default to source_locator_only (no explicit review)."""

    def test_table_formula_figure_all_locator_only(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 总则内容说明文字。"),
            _table_element("e2"),
            _formula_element("e3"),
            _figure_element("e4"),
        ])
        chunks = _chunk(doc)
        by_type = {tuple(c.metadata["element_types"]): c for c in chunks}
        self.assertIn(("table",), by_type)
        self.assertIn(("formula",), by_type)
        self.assertIn(("figure",), by_type)
        self.assertEqual(by_type[("table",)].metadata["usage_policy"], "source_locator_only")
        self.assertEqual(by_type[("formula",)].metadata["usage_policy"], "source_locator_only")
        self.assertEqual(by_type[("figure",)].metadata["usage_policy"], "source_locator_only")

    def test_structured_chunk_carries_quality_needs_review(self):
        doc = _canonical([_table_element("e2")])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["quality_status"], "needs_review")
        self.assertIn("structured_default_locator_only", chunks[0].metadata["policy_reasons"])


class QuarantineNeedsReviewNotAnswerTest(unittest.TestCase):
    """R4: quarantined/needs_review sources never become answer evidence."""

    def test_quarantined_text_element_locator_only(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 一段可读的中文正文内容。",
                          quality_status="quarantined"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "source_locator_only")

    def test_needs_review_text_element_locator_only(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 一段可读的中文正文内容。",
                          quality_status="needs_review"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "source_locator_only")


class MixedChunkStrictestWinsTest(unittest.TestCase):
    """D2: a mixed chunk's policy = strictest among its source elements."""

    def test_answer_plus_needs_review_yields_locator(self):
        # Two short paragraphs in the same section merge into one chunk.
        sp = [{"label": "5.3", "title": "水质"}]
        doc = _canonical([
            _text_element("e1", "clause", "第5.3条 水质要求总则。", section_path=sp,
                          quality_status="passed"),
            _text_element("e2", "paragraph", "短句补充说明一。", section_path=sp,
                          quality_status="needs_review"),
            _text_element("e3", "paragraph", "短句补充说明二。", section_path=sp,
                          quality_status="passed"),
        ])
        chunks = _chunk(doc)
        merged = [c for c in chunks if len(c.element_ids) > 1]
        self.assertTrue(merged, "expected a merged chunk")
        for c in merged:
            self.assertEqual(c.metadata["usage_policy"], "source_locator_only")

    def test_excluded_element_makes_chunk_excluded(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 一段可读的中文正文内容。",
                          usage_policy="excluded"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "excluded")


class ReadableTextStaysAnswerTest(unittest.TestCase):
    """A readable text element answers ONLY when it explicitly carries
    ``quality_status="passed"``. "Looks readable" is a text floor, not a
    quality conclusion -- a text element with no quality field is
    ``needs_review`` / locator-only."""

    def test_passed_text_answers(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 一段可读的中文正文内容。",
                          quality_status="passed"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "answer_and_citation")
        self.assertEqual(chunks[0].metadata["quality_status"], "passed")

    def test_text_without_quality_is_locator(self):
        doc = _canonical([
            _text_element("e1", "clause", "第5.1条 一段可读的中文正文内容。"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "source_locator_only")
        self.assertEqual(chunks[0].metadata["quality_status"], "needs_review")


class UnreadableTextFloorTest(unittest.TestCase):
    """Text readability is a hard floor: unreadable text never answers."""

    def test_unreadable_text_locator_only(self):
        # mojibake/symbol text fails is_answerable_text.
        doc = _canonical([
            _text_element("e1", "clause", "/G01 /G02 /G03 /G04 /G05 /G06 /G07 /G08"),
        ])
        chunks = _chunk(doc)
        self.assertEqual(chunks[0].metadata["usage_policy"], "source_locator_only")


# --------------------------------------------------------------------------- #
# Retriever missing-policy safe default
# --------------------------------------------------------------------------- #

class RetrieverMissingPolicyTest(unittest.TestCase):
    """Qdrant payload missing usage_policy must default to locator-only, not answer."""

    def _to_hit(self, payload: dict):
        from src.retrieval.qdrant_index import QdrantRetrievalIndex
        point = SimpleNamespace(payload=payload, score=0.5)
        return QdrantRetrievalIndex._to_hit(point, score=0.5)

    def test_missing_policy_defaults_to_locator(self):
        payload = {"chunk_id": "c1", "text": "x", "primary_evidence_id": "c1"}
        hit = self._to_hit(payload)
        self.assertEqual(hit.usage_policy, "source_locator_only")

    def test_explicit_answer_preserved(self):
        payload = {"chunk_id": "c1", "text": "x", "usage_policy": "answer_and_citation"}
        hit = self._to_hit(payload)
        self.assertEqual(hit.usage_policy, "answer_and_citation")


# --------------------------------------------------------------------------- #
# Enrich never upgrades policy
# --------------------------------------------------------------------------- #

def _load_enrich_module():
    path = _REPO / "scripts" / "enrich_corpus_v2_payloads.py"
    spec = importlib.util.spec_from_file_location("enrich_corpus_v2_payloads", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EnrichPreservesPolicyTest(unittest.TestCase):
    """D2: enrich must not upgrade an existing policy; missing -> locator-only."""

    def setUp(self):
        self.mod = _load_enrich_module()
        self.md = {
            "file_name": "ex.pdf", "document_kind": "standard", "jurisdictions": ["全国"],
            "standard_number": "T/CECS 758-2020", "source_authority": "", "publication_date": "",
            "effective_from": "", "effective_to": "", "effective_status": "unknown",
            "effective_status_as_of": "",
        }

    def test_existing_locator_preserved(self):
        payload = {"chunk_id": "c1", "source_sha256": SHA, "usage_policy": "source_locator_only"}
        enr = self.mod._build_enrichment(payload, self.md, "", "c1", SHA)
        # enrich must NOT overwrite an existing locator-only with answer.
        self.assertNotEqual(enr.get("usage_policy"), "answer_and_citation")
        # either omitted (preserved via no set_payload) or kept as locator.
        if "usage_policy" in enr:
            self.assertEqual(enr["usage_policy"], "source_locator_only")

    def test_existing_answer_preserved(self):
        payload = {"chunk_id": "c1", "source_sha256": SHA, "usage_policy": "answer_and_citation"}
        enr = self.mod._build_enrichment(payload, self.md, "", "c1", SHA)
        # must not downgrade a legitimately-answerable policy either? Actually
        # enrich must never CHANGE policy in either direction. Verify it does not
        # overwrite to a different value.
        if "usage_policy" in enr:
            self.assertEqual(enr["usage_policy"], "answer_and_citation")

    def test_missing_policy_backfilled_locator(self):
        payload = {"chunk_id": "c1", "source_sha256": SHA}
        enr = self.mod._build_enrichment(payload, self.md, "", "c1", SHA)
        self.assertEqual(enr.get("usage_policy"), "source_locator_only")


# --------------------------------------------------------------------------- #
# Answer path double filter (regression)
# --------------------------------------------------------------------------- #

class AnswerPathDoubleFilterTest(unittest.TestCase):
    """answer path: Qdrant filter + result post-filter both exclude locator-only."""

    def _hit(self, *, evidence_id, usage_policy):
        return SimpleNamespace(
            chunk_id=evidence_id, primary_evidence_id=evidence_id, text="测试证据",
            document_version_id="doc_1", file_name="测试.pdf", physical_pages=[1],
            heading_path=[], usage_policy=usage_policy, source_uri="", jurisdiction="全国",
            document_kind="standard", effective_status="unknown", source_authority="",
            publication_date="", effective_from="", effective_to="",
        )

    def test_locator_only_excluded_from_answer_pack(self):
        from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever

        class FakeIndex:
            def __init__(self):
                self.filters = None
            def search(self, query, *, mode, filters, limit):
                self.filters = filters
                # index would normally honour the filter, but simulate a leaky
                # index that returns a locator-only hit anyway -> post-filter must drop it.
                return [
                    self._hit("ev_answer", "answer_and_citation"),
                    self._hit("ev_leaked_locator", "source_locator_only"),
                ]
            @staticmethod
            def _hit(eid, up):
                return SimpleNamespace(
                    chunk_id=eid, primary_evidence_id=eid, text="测试证据",
                    document_version_id="doc_1", file_name="测试.pdf", physical_pages=[1],
                    heading_path=[], usage_policy=up, source_uri="", jurisdiction="全国",
                    document_kind="standard", effective_status="unknown", source_authority="",
                    publication_date="", effective_from="", effective_to="",
                )

        class FakeEmbedder:
            def embed_query(self, text):
                return [1.0]

        index = FakeIndex()
        retriever = QdrantEvidenceRetriever(
            index=index, embedder=FakeEmbedder(),
            fixed_filters={"usage_policy": "answer_and_citation"}, limit=10,
        )
        pack = asyncio.run(retriever.retrieve(
            question="要求是什么", resolved_scope={}, corpus_version="corpus-test"))
        # Qdrant filter was sent.
        self.assertEqual(index.filters, {"usage_policy": "answer_and_citation"})
        # post-filter dropped the locator-only leak.
        ids = [item.evidence_id for item in pack.evidence]
        self.assertEqual(ids, ["ev_answer"])


# --------------------------------------------------------------------------- #
# Strict synthesis matrix (reviewer-required cases)
# --------------------------------------------------------------------------- #

class StrictSynthesisMatrixTest(unittest.TestCase):
    """final_policy = strictest(explicit|default, quality->policy, text floor).

    answer only when: all sources passed AND all policies answer AND text
    readable AND (structured explicitly review-approved).
    """

    def _policy(self, elements, text="第5.1条 一段可读的中文正文内容。"):
        from src.application.evidence_policy import chunk_usage_policy
        return chunk_usage_policy(elements, text)

    def test_explicit_answer_plus_needs_review_is_locator(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          usage_policy="answer_and_citation",
                          quality_status="needs_review"),
        ])
        self.assertEqual(pol, "source_locator_only")

    def test_explicit_answer_plus_quarantined_is_locator(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          usage_policy="answer_and_citation",
                          quality_status="quarantined"),
        ])
        self.assertEqual(pol, "source_locator_only")

    def test_explicit_answer_plus_missing_quality_is_locator(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          usage_policy="answer_and_citation"),
        ])
        self.assertEqual(pol, "source_locator_only")
        self.assertEqual(qual, "needs_review")

    def test_text_missing_quality_policy_is_locator_needs_review(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。"),
        ])
        self.assertEqual(pol, "source_locator_only")
        self.assertEqual(qual, "needs_review")

    def test_passed_text_answers(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。", quality_status="passed"),
        ])
        self.assertEqual(pol, "answer_and_citation")
        self.assertEqual(qual, "passed")

    def test_passed_plus_excluded_is_excluded(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          usage_policy="excluded", quality_status="passed"),
        ])
        self.assertEqual(pol, "excluded")

    def test_unknown_quality_policy_is_locator(self):
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          usage_policy="bogus", quality_status="bogus"),
        ])
        self.assertEqual(pol, "source_locator_only")
        self.assertEqual(qual, "needs_review")

    def test_structured_passed_without_explicit_policy_still_locator(self):
        # D3: structured content needs an explicit review-approved policy to
        # answer; passed quality alone is not enough.
        pol, qual, _ = self._policy([
            _table_element("e2", quality_status="passed"),
        ])
        self.assertEqual(pol, "source_locator_only")

    def test_structured_answer_without_review_assertion_is_locator(self):
        # D3: passed + explicit answer but NO review/assertion ID -> locator.
        pol, qual, _ = self._policy([
            _table_element("e2", quality_status="passed",
                           usage_policy="answer_and_citation"),
        ])
        self.assertEqual(pol, "source_locator_only")

    def test_structured_answer_with_empty_review_assertion_is_locator(self):
        # D3: empty / whitespace review id does not count.
        pol, qual, _ = self._policy([
            _table_element("e2", quality_status="passed",
                           usage_policy="answer_and_citation",
                           review_assertion_id="   "),
        ])
        self.assertEqual(pol, "source_locator_only")

    def test_structured_answer_with_valid_review_assertion_answers(self):
        # D3: passed + explicit answer + valid review/assertion ID -> answer.
        pol, qual, _ = self._policy([
            _table_element("e2", quality_status="passed",
                           usage_policy="answer_and_citation",
                           review_assertion_id="review-2026-0001"),
        ])
        self.assertEqual(pol, "answer_and_citation")

    def test_mixed_chunk_uncredentialed_structured_is_locator(self):
        # mixed chunk: a passed text answer element + a structured element with
        # no review assertion -> whole chunk locator-only.
        pol, qual, _ = self._policy([
            _text_element("e1", "clause", "正文。",
                          quality_status="passed"),
            _table_element("e2", quality_status="passed",
                           usage_policy="answer_and_citation"),
        ])
        self.assertEqual(pol, "source_locator_only")


# --------------------------------------------------------------------------- #
# Reason code generation (auto, deduped, stable order)
# --------------------------------------------------------------------------- #

class ReasonCodeTest(unittest.TestCase):
    def test_reasons_auto_generated_deduped_stable_order(self):
        from src.application.evidence_policy import chunk_usage_policy
        # three distinct downgrade sources, in a fixed order.
        elements = [
            _text_element("e1", "clause", "正文一。", quality_status="quarantined"),
            _text_element("e2", "clause", "正文二。", quality_status="needs_review"),
            _text_element("e3", "clause", "正文三。"),  # missing -> missing_policy
        ]
        _, _, reasons = chunk_usage_policy(elements, "正文一。正文二。正文三。")
        self.assertEqual(reasons, [
            "quarantined_source",
            "needs_review_source",
            "missing_policy_default_locator_only",
        ])

    def test_reasons_deduped_across_repeated_states(self):
        from src.application.evidence_policy import chunk_usage_policy
        elements = [
            _text_element("e1", "clause", "正文一。", quality_status="quarantined"),
            _text_element("e2", "clause", "正文二。", quality_status="quarantined"),
            _text_element("e3", "clause", "正文三。", quality_status="quarantined"),
        ]
        _, _, reasons = chunk_usage_policy(elements, "正文一。正文二。正文三。")
        self.assertEqual(reasons, ["quarantined_source"])

    def test_unreadable_text_reason_appended(self):
        from src.application.evidence_policy import chunk_usage_policy
        # passed element but unreadable chunk text -> floor downgrades.
        elements = [
            _text_element("e1", "clause", "/G01 /G02", quality_status="passed"),
        ]
        pol, qual, reasons = chunk_usage_policy(elements, "/G01 /G02 /G03 /G04")
        self.assertEqual(pol, "source_locator_only")
        self.assertIn("unreadable_text", reasons)

    def test_passed_answer_has_no_downgrade_reason(self):
        from src.application.evidence_policy import chunk_usage_policy
        elements = [
            _text_element("e1", "clause", "一段可读的中文正文内容。",
                          quality_status="passed"),
        ]
        _, _, reasons = chunk_usage_policy(elements, "一段可读的中文正文内容。")
        self.assertEqual(reasons, [])


# --------------------------------------------------------------------------- #
# Zero-source elements + empty-iterable strictest (R4/R6)
# --------------------------------------------------------------------------- #

class ZeroSourceElementsTest(unittest.TestCase):
    """A chunk with no source elements must never be answer evidence."""

    def test_empty_elements_locator_needs_review(self):
        from src.application.evidence_policy import chunk_usage_policy
        pol, qual, reasons = chunk_usage_policy([], "一段可读的中文正文内容。")
        self.assertEqual(pol, "source_locator_only")
        self.assertEqual(qual, "needs_review")
        self.assertEqual(reasons, ["missing_source_elements"])

    def test_strictest_policy_empty_is_locator(self):
        from src.application.evidence_policy import strictest_policy
        self.assertEqual(strictest_policy([]), "source_locator_only")
        self.assertEqual(strictest_policy(iter([])), "source_locator_only")

    def test_strictest_quality_empty_is_needs_review(self):
        from src.application.evidence_policy import strictest_quality
        self.assertEqual(strictest_quality([]), "needs_review")
        self.assertEqual(strictest_quality(iter([])), "needs_review")

    def test_missing_structured_review_assertion_reason(self):
        from src.application.evidence_policy import chunk_usage_policy
        # passed + answer structured element without review assertion -> locator
        # with the missing_structured_review_assertion reason.
        el = _table_element("e2", quality_status="passed",
                            usage_policy="answer_and_citation")
        pol, qual, reasons = chunk_usage_policy([el], "表1 示例")
        self.assertEqual(pol, "source_locator_only")
        self.assertIn("missing_structured_review_assertion", reasons)


# --------------------------------------------------------------------------- #
# Policy module unit
# --------------------------------------------------------------------------- #

class PolicyModuleUnitTest(unittest.TestCase):
    def test_strictest_policy(self):
        from src.application.evidence_policy import strictest_policy
        self.assertEqual(strictest_policy(["answer_and_citation", "source_locator_only"]),
                         "source_locator_only")
        self.assertEqual(strictest_policy(["source_locator_only", "excluded"]), "excluded")
        self.assertEqual(strictest_policy(["answer_and_citation"]), "answer_and_citation")

    def test_strictest_policy_missing_is_locator(self):
        from src.application.evidence_policy import strictest_policy
        self.assertEqual(strictest_policy(["answer_and_citation", None, ""]),
                         "source_locator_only")

    def test_quality_to_policy(self):
        from src.application.evidence_policy import quality_to_policy
        self.assertEqual(quality_to_policy("passed"), "answer_and_citation")
        self.assertEqual(quality_to_policy("needs_review"), "source_locator_only")
        self.assertEqual(quality_to_policy("quarantined"), "source_locator_only")
        self.assertEqual(quality_to_policy(None), "source_locator_only")

    def test_normalize_policy(self):
        from src.application.evidence_policy import normalize_policy
        self.assertEqual(normalize_policy("answer_and_citation"), "answer_and_citation")
        self.assertEqual(normalize_policy("bogus"), "source_locator_only")
        self.assertEqual(normalize_policy(None), "source_locator_only")


if __name__ == "__main__":
    unittest.main()
