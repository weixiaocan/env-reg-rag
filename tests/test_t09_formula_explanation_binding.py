"""T09 — formula/explanation association (式中 binding) acceptance tests.

Red-light matrix (design approved 2026-09-28, conditional):

R1  four-requirement primary binding (start signal / same section / position
    / stop conditions); any missing requirement -> NO binding, independent
    text chunk kept, explicit ``formula_explanation_binding_ambiguous`` issue.
R2  binding targets must exist and be text elements (domain gate).
R3  at most one PRIMARY binding per explanation element (domain gate).
R4  ``formula_context_ledger`` version gate + binding_count reconciliation;
    bindings without the ledger marker are a bypass -> SchemaError.
R5  legacy artifacts stay byte-identical (digest-excluded fields; real
    published artifact recomputes to the stored ids).
R6  bindings + ledger flip canonical_id, never canonical_content_id.
R7  chunker: ledger docs project with the v2 formula-binding semantics;
    legacy docs keep the v1 projection byte-for-byte (incl. context_text
    embedding and the default projection version).
R8  primary-bound explanation leaves ordinary text chunking; the formula
    chunk carries the explanation exactly once (no duplication either way).
R9  overlong formula units split into shards that EACH carry the formula
    number + the COMPLETE latex body + a contiguous explanation line block,
    with per-shard element_ids / source_spans / usage policy projected from
    the shard's ACTUAL sources.
R9b anchor overflow (number + body alone over the hard cap) -> a SHORT
    locator record only (the complete latex stays in the Canonical element);
    no chunk that reaches the embedder ever exceeds the hard cap.
R10 shard-level strictest-wins policy: a shard containing a needs_review
    explanation source can never answer, even when the formula itself is
    review-credentialed.
R11 cross-page: only the EXPLICIT numbered relation may bind (secondary);
    ambiguous / uncertain relations never bind.
R12 (run-time, not unit) candidate isolation.
R14 projection determinism.
R15 (closure round 3) mixed-stop content: a text element that embeds the
    NEXT clause number / next formula inside itself (real sample 48429f37
    p12 e000131) can never be wholesale-bound as the previous formula's
    explanation -- conservative truncation, body text retained.
R16 multi-shard source_spans may only come from the formula body itself and
    the shard's ACTUALLY-carried explanation elements (never the walk-derived
    context union).
R18 (closure round 4) identical-projection dedupe: the splitter overlap must
    never emit byte-identical chunk texts (real sample 48429f37 p22 e000261);
    ingest refuses any duplicate chunk id (identical OR conflicting).
R17 central emission gate: the single choke point raises on any chunk over
    the hard cap; anchor-exactly-512 + explanation never loses the
    explanation.
"""
from __future__ import annotations

import copy
import json
import os
import unittest

from src.application.canonical_assembly import (
    _PageItem,
    _resolve_explanation_bindings,
    assemble_document,
)
from src.application.canonical_validation import compute_ids, validate_document
from src.domain.canonical_document import SchemaError
from src.domain.v2_chunk import DEFAULT_PROJECTION_VERSION
from src.application.v2_chunker import (
    FORMULA_BINDING_PROJECTION_VERSION,
    _ChunkBuilder,
    _project_text,
    chunk_document,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _CharTokenizer:
    def count(self, text: str) -> int:
        return len(text)


# --------------------------------------------------------------------------- #
# Slot-level helpers (four-requirement resolver)
# --------------------------------------------------------------------------- #

def _slot(kind, idx, page, bbox, text="", ref=None):
    return _PageItem(kind=kind, page=page, bbox=bbox, text=text, ref=ref or {},
                     source_index=idx)


def _formula_slot(idx, page=1, bbox=None):
    return _slot("formula", idx, page, bbox or [60.0, 100.0, 400.0, 130.0],
                 text="Q=V/A", ref={"region_id": "up-formula1"})


def _elem_id_map(*pairs):
    return dict(pairs)


class FourRequirementBindingTest(unittest.TestCase):
    """R1: the resolver itself."""

    def test_bound_when_start_signal_and_same_section(self):
        fx = _formula_slot(0)
        s1 = _slot("text", 1, 1, [60.0, 140.0, 500.0, 160.0], "式中：Q——流量（m³/s）；")
        s2 = _slot("text", 2, 1, [60.0, 165.0, 500.0, 185.0], "V——流速（m/s）；A——过水断面面积。")
        ids = {1: "e000010", 2: "e000011"}
        bindings, issues = _resolve_explanation_bindings(fx, [fx, s1, s2], ids, [])
        self.assertEqual(issues, [])
        self.assertEqual([b["element_id"] for b in bindings], ["e000010", "e000011"])
        self.assertTrue(all(b["role"] == "primary" for b in bindings))
        self.assertTrue(all(b["basis"] == "below_geometry" for b in bindings))
        self.assertEqual([b["order"] for b in bindings], [0, 1])

    def test_no_start_signal_keeps_independent_text(self):
        fx = _formula_slot(0)
        s1 = _slot("text", 1, 1, [60.0, 140.0, 500.0, 160.0], "符号说明如下：Q——流量。")
        bindings, issues = _resolve_explanation_bindings(fx, [fx, s1], {1: "e000010"}, [])
        self.assertEqual(bindings, [])
        self.assertTrue(any("no_start_signal" in i for i in issues))

    def test_section_path_mismatch_is_ambiguous(self):
        from src.ingestion.heading_hierarchy import HeadingNode
        fx = _formula_slot(0)
        s1 = _slot("text", 1, 1, [60.0, 140.0, 500.0, 160.0], "式中：Q——流量。")
        # a high-confidence heading sits BETWEEN formula and explanation, so
        # the explanation's section path contains it while the formula's
        # does not -> not the same section -> never bind.
        headings = [HeadingNode(label="5.5", title=None, level=2,
                                confidence=0.95, source_index=1)]
        bindings, issues = _resolve_explanation_bindings(
            fx, [fx, s1], {1: "e000010"}, headings)
        self.assertEqual(bindings, [])
        self.assertTrue(any("section_path_mismatch" in i for i in issues))

    def test_no_text_below_is_not_ambiguous(self):
        fx = _formula_slot(0)
        bindings, issues = _resolve_explanation_bindings(fx, [fx], {}, [])
        self.assertEqual((bindings, issues), ([], []))

    def test_stop_condition_ends_the_block(self):
        fx = _formula_slot(0)
        s1 = _slot("text", 1, 1, [60.0, 140.0, 500.0, 160.0], "式中：Q——流量。")
        s2 = _slot("text", 2, 1, [60.0, 200.0, 500.0, 220.0], "5.4.3 下一条编号条款。")
        bindings, issues = _resolve_explanation_bindings(fx, [fx, s1, s2], {1: "e1", 2: "e2"}, [])
        self.assertEqual(issues, [])
        self.assertEqual([b["element_id"] for b in bindings], ["e1"])


# --------------------------------------------------------------------------- #
# Domain gates (R2/R3/R4/R6)
# --------------------------------------------------------------------------- #

SHA = "c" * 64


def _span(sid, page=1, bbox=None):
    return {"span_id": sid, "role": "primary", "physical_page": page,
            "bbox": bbox or [66.0, 170.0, 529.0, 238.0], "orientation_degrees": 0}


def _text_element(eid, text, *, quality="passed"):
    el = {
        "element_id": eid, "type": "text", "role": "paragraph", "label": None,
        "section_path": [],
        "content": {"text": text, "list": None},
        "source_spans": [_span(f"s{eid}")],
        "provenance": [{"operation": "extract", "method": "native_text",
                        "engine_id": "native-1", "confidence": 0.9}],
        "links": [],
        "quality_status": quality, "policy_reasons": [], "quality_issues": [],
        "source_region_ids": [], "processing_config_ids": [],
        "execution_status": "completed", "consumed_sources": [],
        "crop_assets": [], "cross_page_relations": [],
    }
    return el


def _formula_element(eid, *, bindings=None, latex="Q=V/A", number="3.1-1"):
    el = {
        "element_id": eid, "type": "formula", "role": "display_formula",
        "label": number, "section_path": [],
        "content": {"formula_number": number, "latex": latex,
                    "recognized_formula": None, "context_text": "式中：Q——流量。"},
        "source_spans": [_span(f"s{eid}", bbox=[60.0, 100.0, 400.0, 130.0])],
        "provenance": [{"operation": "recognize", "method": "formula_recognition",
                        "engine_id": "native-1", "confidence": 0.9}],
        "links": [],
        "quality_status": "needs_review", "policy_reasons": ["structured_default_locator_only"],
        "quality_issues": [], "source_region_ids": ["up-f1"],
        "processing_config_ids": ["cfg-1"], "execution_status": "completed",
        "consumed_sources": [], "crop_assets": [], "cross_page_relations": [],
    }
    if bindings is not None:
        el["explanation_bindings"] = bindings
    return el


def _doc(elements, *, ledger=None):
    return {
        "schema_version": "v2.canonical/1.0",
        "canonical_id": "canonical-sha256:" + "0" * 64,
        "canonical_content_id": "canonical-content-sha256:" + "0" * 64,
        "metadata_fingerprint": "metadata-sha256:" + "0" * 64,
        "document_id": f"pdf-sha256:{SHA}",
        "source": {"sha256": SHA, "file_name": "t.pdf",
                   "media_type": "application/pdf", "page_count": 1,
                   "source_uri": None},
        "metadata": {
            "title": "t", "alternate_titles": [], "language": "zh-CN",
            "identifiers": [{"scheme": "standard_number", "value": "T 1"}],
            "document_kind": "standard", "jurisdictions": [],
            "issuing_authorities": [], "publication_date": None,
            "effective_from": None, "effective_to": None,
            "effective_status": "current",
            "effective_status_as_of": "2026-09-21",
            "registry_snapshot_id": "source-registry-sha256:" + "r" * 64,
        },
        "generation": {
            "run_id": "r1", "created_at": "2026-09-21T00:00:00Z",
            "pipeline": "v2-pdf-pipeline", "pipeline_version": "0.1.0",
            "config_sha256": "9" * 64,
            "engines": [{"engine_id": "native-1", "name": "pymupdf", "version": "1.23"}],
        },
        "pages": [{"physical_page": 1, "printed_label": None, "width": 595.2,
                   "height": 841.8, "unit": "pt", "rotation_degrees": 0,
                   "parse_status": "parsed", "parse_error": None,
                   "extraction_route": "native", "decision_status": "approved",
                   "decision_reasons": [], "engine_id": "native-1",
                   "confidence": None, "config_hash": None}],
        "elements": elements,
    }
    # note: `ledger` is merged in by callers that need it (see _doc_with_ledger)


def _doc_with_ledger(elements, binding_count):
    d = _doc(elements)
    d["formula_context_ledger"] = {"version": "t09-1", "binding_count": binding_count}
    return d


_PRIMARY = {"element_id": "e000001", "region_id": "up-t1",
            "basis": "below_geometry", "role": "primary", "order": 0}


class DomainGateTest(unittest.TestCase):
    def _base_elements(self, bindings=None):
        return [
            _text_element("e000001", "式中：Q——流量。"),
            _formula_element("e000002", bindings=bindings),
        ]

    def test_valid_binding_and_ledger_validate(self):
        doc = _doc_with_ledger(self._base_elements([_PRIMARY]), 1)
        ids = compute_ids(doc)
        doc["canonical_id"] = ids["canonical_id"]
        doc["canonical_content_id"] = ids["canonical_content_id"]
        doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
        validate_document(doc)

    def test_r2_phantom_target_rejected(self):
        bad = dict(_PRIMARY, element_id="e999999")
        doc = _doc_with_ledger(self._base_elements([bad]), 1)
        with self.assertRaises(SchemaError):
            compute_ids(doc)

    def test_r3_double_primary_rejected(self):
        el1 = _text_element("e000001", "式中：Q——流量。")
        f1 = _formula_element("e000002", bindings=[dict(_PRIMARY)])
        f2 = _formula_element("e000003", bindings=[dict(_PRIMARY)],
                              latex="R=V/Q", number="3.1-2")
        doc = _doc_with_ledger([el1, f1, f2], 2)
        with self.assertRaises(SchemaError):
            compute_ids(doc)

    def test_r4_wrong_version_rejected(self):
        doc = _doc_with_ledger(self._base_elements([_PRIMARY]), 1)
        doc["formula_context_ledger"]["version"] = "t08-9"
        with self.assertRaises(SchemaError):
            compute_ids(doc)

    def test_r4_binding_count_mismatch_rejected(self):
        doc = _doc_with_ledger(self._base_elements([_PRIMARY]), 2)
        with self.assertRaises(SchemaError):
            compute_ids(doc)

    def test_r4_bindings_without_ledger_is_a_bypass(self):
        doc = _doc(self._base_elements([_PRIMARY]))
        with self.assertRaises(SchemaError):
            compute_ids(doc)

    def test_r5_absent_fields_keep_identity(self):
        """A document WITHOUT the new fields recomputes to the same ids as the
        same document where the fields are present-but-empty (digest exclusion
        keeps legacy artifacts byte-identical)."""
        d1 = _doc(self._base_elements())
        i1 = compute_ids(d1)
        d2 = copy.deepcopy(d1)
        for e in d2["elements"]:
            e["explanation_bindings"] = []
        d2["formula_context_ledger"] = None
        i2 = compute_ids(d2)
        self.assertEqual(i1, i2)

    def test_r6_bindings_flip_canonical_id_not_content_id(self):
        plain = _doc(self._base_elements())
        bound = _doc_with_ledger(self._base_elements([_PRIMARY]), 1)
        i_plain = compute_ids(plain)
        i_bound = compute_ids(bound)
        self.assertEqual(i_plain["canonical_content_id"],
                         i_bound["canonical_content_id"])
        self.assertNotEqual(i_plain["canonical_id"], i_bound["canonical_id"])

    def test_roundtrip_preserves_ledger_and_bindings(self):
        from src.domain.canonical_document import CanonicalDocument
        doc = _doc_with_ledger(self._base_elements([_PRIMARY]), 1)
        ids = compute_ids(doc)
        doc["canonical_id"] = ids["canonical_id"]
        doc["canonical_content_id"] = ids["canonical_content_id"]
        doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
        parsed = CanonicalDocument.from_dict(doc)
        out = parsed.to_dict()
        self.assertEqual(out["formula_context_ledger"],
                         {"version": "t09-1", "binding_count": 1})
        self.assertEqual(out["elements"][1]["explanation_bindings"], [_PRIMARY])


# --------------------------------------------------------------------------- #
# Chunker (R7/R8/R9/R9b/R10/R14)
# --------------------------------------------------------------------------- #

def _chunk_canonical(elements, *, ledger=True, page_count=1):
    doc = _doc(elements)
    doc["pages"] = [{"physical_page": i, "printed_label": None, "width": 595.2,
                     "height": 841.8, "unit": "pt", "rotation_degrees": 0,
                     "parse_status": "parsed", "parse_error": None,
                     "extraction_route": "native", "decision_status": "approved",
                     "decision_reasons": [], "engine_id": "native-1",
                     "confidence": None, "config_hash": None}
                    for i in range(1, page_count + 1)]
    if ledger:
        n = sum(len(e.get("explanation_bindings") or []) for e in elements)
        doc["formula_context_ledger"] = {"version": "t09-1", "binding_count": n}
    return doc


def _bound_formula(eid, explanation_ids, *, latex="Q=V/A", number="3.1-1",
                   context="式中：Q——流量。"):
    el = _formula_element(eid, latex=latex, number=number)
    el["content"]["context_text"] = context
    el["explanation_bindings"] = [
        {"element_id": t, "region_id": None, "basis": "below_geometry",
         "role": "primary", "order": i}
        for i, t in enumerate(explanation_ids)]
    return el


def _chunk(doc, budget=480):
    return chunk_document(doc, tokenizer=_CharTokenizer(),
                          token_budget=budget, overlap=0)


class ChunkerLedgerTest(unittest.TestCase):
    def test_r7_legacy_doc_keeps_v1_projection(self):
        doc = _chunk_canonical(
            [_text_element("e1", "式中：Q——流量。"), _bound_formula("f1", [])],
            ledger=False)
        chunks = _chunk(doc)
        self.assertEqual({c.projection_version for c in chunks},
                         {DEFAULT_PROJECTION_VERSION})
        # legacy formula chunk embeds context_text verbatim
        fchunk = next(c for c in chunks if "f1" in c.element_ids)
        self.assertIn("式中：Q——流量。", fchunk.text)

    def test_r7b_ledger_doc_switches_projection_version(self):
        doc = _chunk_canonical([_text_element("e1", "独立段落。"),
                                _bound_formula("f1", [])])
        chunks = _chunk(doc)
        self.assertEqual({c.projection_version for c in chunks},
                         {FORMULA_BINDING_PROJECTION_VERSION})

    def test_r7c_ledger_unbound_formula_does_not_duplicate_context(self):
        doc = _chunk_canonical([_text_element("e1", "独立段落文字。"),
                                _bound_formula("f1", [])])
        chunks = _chunk(doc)
        fchunk = next(c for c in chunks if "f1" in c.element_ids)
        self.assertNotIn("式中：Q——流量。", fchunk.text)

    def test_r8_primary_bound_explanation_leaves_normal_chunking(self):
        doc = _chunk_canonical([
            _bound_formula("f1", ["e1"]),
            _text_element("e1", "式中：Q——流量。"),
        ])
        chunks = _chunk(doc)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertEqual(len(fchunks), 1)
        self.assertIn("式中：Q——流量。", fchunks[0].text)
        self.assertIn("e1", fchunks[0].element_ids)
        # the explanation no longer produces its own standalone text chunk
        standalone = [c for c in chunks if c.element_ids == ["e1"]]
        self.assertEqual(standalone, [])

    def test_r9_overlong_shards_carry_number_and_full_body(self):
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        expl = _text_element("e1", "式中：" + line * 8)
        doc = _chunk_canonical([_bound_formula("f1", ["e1"], latex="Q=V/A"),
                                expl])
        chunks = _chunk(doc, budget=60)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertGreater(len(fchunks), 1)
        tok = _CharTokenizer()
        for c in fchunks:
            self.assertLessEqual(len(c.text), 512)
            self.assertIn("[公式 3.1-1]", c.text)
            self.assertIn("Q=V/A", c.text)   # complete body in EVERY shard
        # per-shard source projection: every shard carries the formula, and
        # the shards jointly cover the explanation span exactly once via the
        # actual explanation sources
        joined = "\n".join(c.text for c in fchunks)
        self.assertIn(line, joined)
        spans = [(s.physical_page, tuple(s.bbox)) for c in fchunks
                 for s in c.source_spans]
        # formula span on EVERY shard; explanation span on every shard too
        # (all shards carry explanation lines here)
        self.assertIn((1, (60.0, 100.0, 400.0, 130.0)), spans)
        self.assertIn((1, (66.0, 170.0, 529.0, 238.0)), spans)

    def test_r10_shard_policy_is_strictest_over_actual_sources(self):
        line = "Q——流量，单位时间内通过过水断面的流体体积，需要特别说明；"
        bad = _text_element("e1", "式中：" + line * 6, quality="needs_review")
        good = _text_element("e2", "V——流速，水流通过过水断面的平均速度，需要特别说明；")
        f = _bound_formula("f1", ["e1", "e2"])
        f["quality_status"] = "passed"
        f["policy_reasons"] = ["review_assertion:review-42"]
        f["quality_issues"] = []
        doc = _chunk_canonical([f, bad, good])
        chunks = _chunk(doc, budget=100)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertGreater(len(fchunks), 1)
        # any shard that actually includes the needs_review explanation source
        # can never answer; the formula alone is credentialed
        for c in fchunks:
            if "e1" in c.element_ids:
                self.assertNotEqual(c.metadata["usage_policy"], "answer_and_citation")
            else:
                self.assertEqual(c.metadata["usage_policy"], "answer_and_citation")
        self.assertTrue(all(c.metadata["usage_policy"] != "excluded"
                            for c in fchunks))

    def test_r9b_anchor_overflow_never_truncates_latex(self):
        body = "Q=V/A" + "+Q_{rivulets}" * 40
        f = _bound_formula("f1", ["e1"], latex=body)
        doc = _chunk_canonical([f, _text_element("e1", "式中：Q——流量。")])
        # the COMPLETE body stays in the canonical element
        self.assertEqual(
            next(e for e in doc["elements"] if e["element_id"] == "f1")
            ["content"]["latex"], body)
        chunks = _chunk(doc)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertEqual(len(fchunks), 1)
        # short locator record only -- the over-limit latex never enters the
        # embedder stream
        self.assertNotIn(body, fchunks[0].text)
        self.assertIn("canonical-sha256:", fchunks[0].text)
        self.assertIn("anchor_exceeds_token_cap", fchunks[0].metadata["policy_reasons"])
        self.assertEqual(fchunks[0].metadata["quality_status"], "needs_review")
        # the explanation stays retrievable as an independent chunk
        self.assertTrue(any("式中：Q——流量。" == c.text
                            for c in chunks if c.element_ids == ["e1"]))

    def test_r14_projection_is_deterministic(self):
        doc = _chunk_canonical([_bound_formula("f1", ["e1"]),
                                _text_element("e1", "式中：Q——流量。")])
        a = [(c.chunk_id, c.text) for c in _chunk(doc)]
        b = [(c.chunk_id, c.text) for c in _chunk(doc)]
        self.assertEqual(a, b)


# --------------------------------------------------------------------------- #
# Cross-page relations (R11) + real-corpus integration
# --------------------------------------------------------------------------- #

class CrossPageBindingTest(unittest.TestCase):
    def test_r11_explicit_relation_binds_secondary(self):
        f = _bound_formula("f1", ["e1"])
        f["cross_page_relations"] = [{
            "relation_id": "frel-1",
            "kind": "explicit_numbered_formula_parameter_reference",
            "basis": "literal_numbered_parameter_marker_unique_formula_in_document",
            "verified": False, "association_status": None,
            "source": {"region_id": "up-t9", "element_id": "pi-e9",
                       "physical_page": 2, "bbox": [10.0, 10.0, 100.0, 50.0],
                       "text": "式(3.1-1)中：V——流速。", "formula_number": None},
            "target": None, "source_span_id": None, "target_element_id": None,
        }]
        # the referenced explanation lives on page 2 as its own element
        doc = _doc_with_ledger([
            f,
            _text_element("e1", "式中：Q——流量。"),
            _page2_text("e2"),
        ], 2)
        from src.application.canonical_assembly import (
            _resolve_cross_page_explanation_bindings,
        )
        elements = doc["elements"]
        bindings, issues = _resolve_cross_page_explanation_bindings(
            f, {e["element_id"]: e for e in elements})
        self.assertEqual(issues, [])
        self.assertEqual(len(bindings), 2)
        secondary = [b for b in bindings if b["role"] == "secondary"]
        self.assertEqual([b["element_id"] for b in secondary], ["e2"])
        self.assertEqual(secondary[0]["basis"], "explicit_number_reference")

    def test_r11_ambiguous_relation_never_binds(self):
        f = _bound_formula("f1", ["e1"])
        f["cross_page_relations"] = [{
            "relation_id": "frel-2",
            "kind": "ambiguous_numbered_formula_parameter_reference",
            "basis": None, "verified": False,
            "association_status": "uncertain",
            "source": {"region_id": "up-t9", "element_id": "pi-e9",
                       "physical_page": 2, "bbox": [10.0, 10.0, 100.0, 50.0],
                       "text": "式中某参数说明", "formula_number": None},
            "target": None, "source_span_id": None, "target_element_id": None,
        }]
        from src.application.canonical_assembly import (
            _resolve_cross_page_explanation_bindings,
        )
        elements = [f, _text_element("e1", "式中：Q——流量。"),
                    _page2_text("e2")]
        bindings, issues = _resolve_cross_page_explanation_bindings(
            f, {e["element_id"]: e for e in elements})
        self.assertFalse(any(b["role"] == "secondary" for b in bindings))


def _page2_text(eid):
    el = _text_element(eid, "式(3.1-1)中：V——流速。")
    el["source_spans"] = [{"span_id": f"s{eid}", "role": "primary",
                           "physical_page": 2,
                           "bbox": [10.0, 10.0, 100.0, 50.0],
                           "orientation_degrees": 0}]
    return el


class RealCorpusIntegrationTest(unittest.TestCase):
    """CECS758 end-to-end: ledger + binding reconciliation + validation."""

    @classmethod
    def setUpClass(cls):
        from src.application.canonical_assembly import load_formula_pages
        sha = "f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941"
        pi = os.path.join(REPO, "data", "canonical",
                          "corpus-37456321a968-documents.jsonl")
        page_doc = None
        with open(pi, encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                if d["sha256"] == sha:
                    page_doc = d
                    break
        if page_doc is None:
            raise unittest.SkipTest("CECS758 page-intermediate not available")
        cache = os.path.join(REPO, "data", "model_runtime", "corpus_formulas",
                             "5d3264515515e228")
        formula_pages, _ = load_formula_pages(sha, range(1, len(page_doc["pages"]) + 1),
                                              cache_dir=cache)
        cls.doc = assemble_document(page_doc, formula_pages=formula_pages)

    def test_ledger_present_and_reconciled(self):
        ledger = self.doc.get("formula_context_ledger")
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["version"], "t09-1")
        total = sum(len(e.get("explanation_bindings") or [])
                    for e in self.doc["elements"] if e["type"] == "formula")
        self.assertEqual(ledger["binding_count"], total)

    def test_bindings_resolve_to_text_elements(self):
        by_id = {e["element_id"]: e for e in self.doc["elements"]}
        for e in self.doc["elements"]:
            for b in e.get("explanation_bindings") or []:
                self.assertIn(b["element_id"], by_id)
                self.assertEqual(by_id[b["element_id"]]["type"], "text")

    def test_at_least_one_primary_binding_in_real_corpus(self):
        bound = [e for e in self.doc["elements"]
                 if any(b["role"] == "primary"
                        for b in e.get("explanation_bindings") or [])]
        self.assertGreater(len(bound), 0)

    def test_context_text_matches_bound_elements(self):
        import re as _re
        by_id = {e["element_id"]: e for e in self.doc["elements"]}
        for e in self.doc["elements"]:
            if e["type"] != "formula" or not e.get("explanation_bindings"):
                continue
            ctx = e["content"].get("context_text") or ""
            if not ctx:
                continue
            joined = "\n".join(
                by_id[b["element_id"]]["content"]["text"]
                for b in sorted(e["explanation_bindings"],
                                key=lambda x: x["order"])
                if b["role"] == "primary")
            self.assertEqual(_re.sub(r"\s+", "", joined),
                             _re.sub(r"\s+", "", ctx))


class LegacyArtifactIdentityTest(unittest.TestCase):
    """R5: the published baseline artifact recomputes to its stored ids."""

    def test_published_baseline_ids_unchanged(self):
        base = os.path.join(REPO, "data", "canonical", "v2",
                            "corpus-069284e6d60e")
        if not os.path.isdir(base):
            self.skipTest("published baseline artifacts not present")
        path = None
        for name in sorted(os.listdir(base)):
            if name.endswith(".canonical.json"):
                path = os.path.join(base, name)
                break
        if path is None:
            self.skipTest("no canonical artifact in baseline")
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        ids = compute_ids(doc)
        self.assertEqual(ids["canonical_id"], doc["canonical_id"])
        self.assertEqual(ids["canonical_content_id"], doc["canonical_content_id"])


# --------------------------------------------------------------------------- #
# Remediation round 2 (review 2026-09-28, conditional)
# --------------------------------------------------------------------------- #

class RemediationTest(unittest.TestCase):
    """内容与来源一致 / 每段说明恰好投影一次 / chunk <= 512."""

    def test_u1_unbound_formula_drops_geometry_guess(self):
        """未绑定公式不得保留几何猜来的 context_text / context spans."""
        from src.application.canonical_assembly import (
            _drop_unbound_formula_context,
        )
        el = {
            "element_id": "f1", "type": "formula",
            "content": {"formula_number": "3.1-1", "latex": "Q=V/A",
                        "recognized_formula": None,
                        "context_text": "式中：Q——流量。"},
            "source_spans": [
                {"span_id": "s1", "role": "primary", "physical_page": 1,
                 "bbox": [60.0, 100.0, 400.0, 130.0], "orientation_degrees": 0},
                {"span_id": "s2", "role": "context", "physical_page": 1,
                 "bbox": [66.0, 170.0, 529.0, 238.0], "orientation_degrees": 0},
            ],
            "quality_issues": [],
        }
        cand = _text_element("e1", "式中：Q——流量。")
        issue = _drop_unbound_formula_context(el, ["e1"], {"e1": cand})
        self.assertIsNone(el["content"]["context_text"])
        self.assertEqual([s["role"] for s in el["source_spans"]], ["primary"])
        self.assertEqual(issue, "formula_context_text_dropped:unbound")
        self.assertIn("formula_context_text_dropped:unbound", el["quality_issues"])
        # the unbound candidate is marked so the chunker isolates it
        self.assertIn("formula_explanation_unbound:f1", cand["quality_issues"])

    def test_u1b_unbound_formula_without_context_is_silent(self):
        """No candidates -> no strip issue (nothing was guessed)."""
        from src.application.canonical_assembly import (
            _drop_unbound_formula_context,
        )
        el = {"element_id": "f1", "type": "formula",
              "content": {"context_text": None},
              "source_spans": [{"span_id": "s1", "role": "primary",
                                "physical_page": 1, "bbox": [1, 2, 3, 4],
                                "orientation_degrees": 0}],
              "quality_issues": []}
        issue = _drop_unbound_formula_context(el, [], {})
        self.assertIsNone(issue)
        self.assertEqual(el["quality_issues"], [])
        self.assertIsNone(el["content"]["context_text"])

    def test_u2_unbound_explanation_not_merged_into_following_text(self):
        """未绑定说明不得误并入后文 -- it stays its own chunk."""
        f = _bound_formula("f1", [])  # unbound (ledger doc)
        cand = _text_element("e1", "式中：Q——流量。")
        cand["quality_issues"] = ["formula_explanation_unbound:f1"]
        after = _text_element("e2", "后续条款正文，继续描述系统要求与验收规则。")
        doc = _chunk_canonical([f, cand, after])
        chunks = _chunk(doc)
        cand_chunks = [c for c in chunks if "e1" in c.element_ids]
        self.assertEqual(len(cand_chunks), 1)
        # the explanation chunk carries ONLY the explanation
        self.assertEqual(cand_chunks[0].element_ids, ["e1"])
        self.assertNotIn("后续条款正文", cand_chunks[0].text)
        # the following text does not absorb the explanation either
        after_chunks = [c for c in chunks if "e2" in c.element_ids]
        self.assertTrue(after_chunks)
        for c in after_chunks:
            self.assertNotIn("式中：Q——流量。", c.text)

    def test_u3_long_section_prefix_no_overlimit_no_text_loss(self):
        """长章节前缀：prefix 超限 -> 丢 prefix，绝不丢 latex、绝不超限。"""
        long_path = [{"label": f"附录Z第{i:03d}条超长章节标题用于测试前缀溢出场景",
                      "level": 3, "confidence": 0.95}
                     for i in range(30)]
        f = _bound_formula("f1", [])
        f["section_path"] = long_path
        doc = _chunk_canonical([f])
        chunks = _chunk(doc)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertTrue(fchunks)
        for c in fchunks:
            self.assertLessEqual(len(c.text), 512)
            self.assertIn("[公式 3.1-1]", c.text)
            self.assertIn("Q=V/A", c.text)

    def test_u4_anchor_overflow_keeps_canonical_and_downgrades(self):
        """超限公式只保留完整 Canonical + 安全短定位记录；说明保留独立 chunk."""
        body = "Q=V/A" + "+Q_{rivulets}" * 40
        f = _bound_formula("f1", ["e1"], latex=body)
        expl = _text_element("e1", "式中：Q——流量。")
        doc = _chunk_canonical([f, expl])
        # canonical keeps the COMPLETE latex verbatim
        self.assertEqual(
            next(e for e in doc["elements"] if e["element_id"] == "f1")
            ["content"]["latex"], body)
        chunks = _chunk(doc)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertEqual(len(fchunks), 1)
        # the over-limit chunk is never sent to the embedder: only a SHORT
        # locator record is emitted, referencing the canonical record that
        # holds the complete latex
        self.assertNotIn(body, fchunks[0].text)
        self.assertIn("canonical-sha256:", fchunks[0].text)
        self.assertLessEqual(len(fchunks[0].text), 512)
        self.assertEqual(fchunks[0].metadata["usage_policy"],
                         "source_locator_only")       # explicit downgrade
        self.assertIn("anchor_exceeds_token_cap",
                      fchunks[0].metadata["policy_reasons"])
        self.assertEqual(fchunks[0].metadata["quality_status"], "needs_review")
        # the explanation stays retrievable as an independent chunk
        self.assertTrue([c for c in chunks if c.element_ids == ["e1"]])

    def test_u5_empty_explanation_still_emits_anchor(self):
        """空说明（投影为空）不得丢公式文本 -- anchor shard 仍然输出。"""
        empty = _text_element("e1", "   ")
        f = _bound_formula("f1", ["e1"])
        doc = _chunk_canonical([f, empty])
        chunks = _chunk(doc)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertEqual(len(fchunks), 1)
        self.assertIn("[公式 3.1-1]", fchunks[0].text)
        self.assertIn("Q=V/A", fchunks[0].text)

    def test_u6_explanation_projected_exactly_once(self):
        """每段说明恰好投影一次 -- 无重复、无遗漏。"""
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        e1 = _text_element("e1", "式中：" + line)
        e2 = _text_element("e2", "n0——地面集水距离（m）；")
        doc = _chunk_canonical([_bound_formula("f1", ["e1", "e2"]), e1, e2])
        chunks = _chunk(doc)
        all_text = "\n".join(c.text for c in chunks)
        self.assertEqual(all_text.count(line), 1)
        self.assertEqual(all_text.count("n0——地面集水距离（m）；"), 1)
        # exactly one chunk carries the explanation alongside the formula
        self.assertEqual(len([c for c in chunks if "f1" in c.element_ids]), 1)

    def test_u7_page_furniture_never_bound(self):
        """页码/页脚是结构边界，不是说明 -- 不得绑定，也不进 context_text."""
        fx = _slot("formula", 0, 1, [60.0, 100.0, 400.0, 130.0], "", {"latex": "Q=V/A"})
        t1 = _slot("text", 1, 1, [66.0, 170.0, 529.0, 238.0], "式中：Q——流量。")
        pagenum = _slot("text", 2, 1, [66.0, 260.0, 529.0, 275.0], "· 9")
        slots = [fx, t1, pagenum]
        slot_element_ids = {1: "e1", 2: "e9"}
        bindings, issues = _resolve_explanation_bindings(
            fx, slots, slot_element_ids, [])
        self.assertEqual(issues, [])
        self.assertEqual([b["element_id"] for b in bindings], ["e1"])
        # the context walk excludes page furniture entirely
        from src.application.canonical_assembly import _gather_formula_context
        from src.application.canonical_assembly import _Counter as _C
        ctx, spans = _gather_formula_context(fx, slots, _C("s"))
        self.assertIsNotNone(ctx)
        self.assertNotIn("9", ctx.replace(" ", "").replace("：", ""))

    def test_u8_furniture_alone_below_formula_gives_no_binding(self):
        """公式下方只有页码 -> 无候选、无绑定、无歧义问题."""
        fx = _slot("formula", 0, 1, [60.0, 100.0, 400.0, 130.0], "", {})
        pagenum = _slot("text", 1, 1, [66.0, 260.0, 529.0, 275.0], "7")
        bindings, issues = _resolve_explanation_bindings(
            fx, [fx, pagenum], {1: "e1"}, [])
        self.assertEqual(bindings, [])
        self.assertEqual(issues, [])

    def test_u6b_shard_projection_exactly_once(self):
        """超长拆分：每行说明按原文出现次数在全部 shards 中恰好出现。

        第 5 轮修正：8 行完全相同的说明也必须按出现次数保留 8 次——原文
        出现位置不同即为不同来源，分块身份必须确定性区分（不得去重、不得
        先到先赢）。"""
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        e1 = _text_element("e1", "式中：" + "\n".join([line] * 8))
        doc = _chunk_canonical([_bound_formula("f1", ["e1"], latex="Q=V/A"), e1])
        chunks = _chunk(doc, budget=60)
        joined = "\n".join(c.text for c in chunks)
        self.assertEqual(joined.count(line), 8)
        ids = [c.chunk_id for c in chunks]
        self.assertEqual(len(ids), len(set(ids)),
                         "occurrence-distinct shards must have unique ids")
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)


class ClosureRound3Test(unittest.TestCase):
    """收口第 3 轮（真实样例 48429f37 p12 e000128 驱动）。

    R15 混排内容保守截断；R16 逐片 span 来源；R17 中央发块门禁 +
    刚好 512 + 说明。
    """

    # -- helpers ------------------------------------------------------------

    def _bbox_of(self, element):
        return tuple(element["source_spans"][0]["bbox"])

    # -- R15: mixed-stop content (real 48429f37 p12 e000128 sample) ---------

    def test_r15_real_48429f37_mixed_clause_never_wholesale_bound(self):
        """同一文本元素内出现 5.8.3/下一公式 -> 不得整元素绑定为上一公式说明；
        无法忠实拆分并定位时保守不绑定、正文保留。"""
        fx = _slot("formula", 0, 12, [244.85, 158.38, 334.90, 169.22],
                   "\\rho=n_{2}\\div S\\times100\\%", {"region_id": "up-f"})
        # VERBATIM texts of 48429f37 p12 elements e000129..e000133 (the
        # explanation block whose later members were OCR-merged with the next
        # clause "5.8.3" and the next formula's intro).
        s1 = _slot("text", 1, 12, [77.63, 176.48, 111.58, 190.61], "式中：")
        s2 = _slot("text", 2, 12, [77.63, 196.26, 351.60, 226.21],
                   "n2——地块单元中混错接点数（居民小区、公共建筑等）；\nS——地块面积，m")
        s3 = _slot("text", 3, 12, [56.65, 211.98, 524.49, 255.64],
                   "2或ha。\n5.8.3 定性评估后，宜按雨水管道中污水混错接、污水管道中雨水混错接"
                   "分类，并分别进行定量计算。\n单元混错接状况可根据混错接密度（M）和混错接水量比"
                   "（C）来确定，确定方法如下：")
        s4 = _slot("text", 4, 12, [56.66, 258.80, 529.68, 317.74],
                   "𝑀𝑀污= 𝑛𝑛污÷ 𝑁𝑁× 100% ········· (3)")
        s5 = _slot("text", 5, 12, [77.89, 260.68, 293.30, 271.24],
                   "雨水管道中污水混错接可依据下式进行判定：")
        ids = {1: "e000129", 2: "e000130", 3: "e000131", 4: "e000132", 5: "e000133"}
        bindings, issues = _resolve_explanation_bindings(
            fx, [fx, s1, s2, s3, s4, s5], ids, [])
        # only the PURE explanation elements stay bound; the mixed element and
        # everything after it are conservatively left as body text
        self.assertEqual([b["element_id"] for b in bindings],
                         ["e000129", "e000130"])
        self.assertTrue(any("mixed_stop_content" in i for i in issues),
                        f"expected mixed_stop_content issue, got {issues!r}")

    def test_r15b_mixed_content_in_first_candidate_binds_nothing(self):
        """首候选自身混排（式中 + 下一条款号）-> 整体保守不绑定，正文保留."""
        fx = _formula_slot(0)
        s1 = _slot("text", 1, 1, [60.0, 140.0, 500.0, 160.0],
                   "式中：Q——流量（m³/s）。\n5.8.3 定性评估后应分别进行定量计算。")
        bindings, issues = _resolve_explanation_bindings(
            fx, [fx, s1], {1: "e1"}, [])
        self.assertEqual(bindings, [])
        self.assertTrue(any("mixed_stop_content" in i for i in issues))

    # -- R16: per-shard span provenance --------------------------------------

    def test_r16_shard_spans_only_formula_and_own_explanations(self):
        """多 shard：每片 spans 只能来自公式本体与该片实际说明元素."""
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        e1 = _text_element("e1", "式中：" + "\n".join([line] * 6))
        e1["source_spans"] = [_span("se1", bbox=[66.0, 170.0, 529.0, 238.0])]
        e2 = _text_element("e2", "V——流速（m/s）。")
        e2["source_spans"] = [_span("se2", bbox=[66.0, 240.0, 529.0, 260.0])]
        f = _bound_formula("f1", ["e1", "e2"], latex="Q=V/A")
        f["source_spans"] = [
            _span("sf", bbox=[60.0, 100.0, 400.0, 130.0]),
            {"span_id": "sc1", "role": "context", "physical_page": 1,
             "bbox": [66.0, 170.0, 529.0, 238.0], "orientation_degrees": 0},
            {"span_id": "sc2", "role": "context", "physical_page": 1,
             "bbox": [66.0, 240.0, 529.0, 260.0], "orientation_degrees": 0},
        ]
        doc = _chunk_canonical([f, e1, e2])
        by_id = {e["element_id"]: e for e in doc["elements"]}
        chunks = _chunk(doc, budget=100)
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertGreater(len(fchunks), 1)
        shard_texts = []
        for c in fchunks:
            allowed = set()
            for eid in c.element_ids:
                for s in by_id[eid]["source_spans"]:
                    # a shard may only cite the formula BODY itself
                    # (role=primary) plus the shard's actually-carried
                    # explanation elements' own spans -- never the
                    # walk-derived context union
                    if eid == "f1" and s.get("role") != "primary":
                        continue
                    allowed.add((s["physical_page"], tuple(s["bbox"])))
            shard_texts.append(c.text)
            for s in c.source_spans:
                key = (s.physical_page, tuple(s.bbox))
                self.assertIn(
                    key, allowed,
                    f"shard {c.chunk_id} carries span {key} of an element it "
                    f"does not actually contain (elements={c.element_ids})")
        # the walk-derived context union must not leak into any shard
        self.assertIn((1, (66.0, 240.0, 529.0, 260.0)),
                      {(s.physical_page, tuple(s.bbox)) for c in fchunks
                       for s in c.source_spans if "e2" in c.element_ids})
        # still exactly-once + anchor in every shard
        joined = "\n".join(shard_texts)
        self.assertEqual(joined.count(line), 6)
        for t in shard_texts:
            self.assertIn("Q=V/A", t)

    # -- R17: central emission gate ------------------------------------------

    def test_r17_central_gate_raises_on_overlimit_chunk(self):
        """中央发块门禁：任何超过 512 的 chunk 在唯一发块点被硬拒."""
        from src.application.v2_chunker import _ChunkBuilder
        doc = _chunk_canonical([_text_element("e1", "独立段落。")])
        builder = _ChunkBuilder(canonical=doc, projection_version="p",
                                token_budget=480, overlap=0,
                                count_fn=len, valid_pages={1})
        with self.assertRaises(ValueError):
            builder._emit_chunk(
                element_ids=["e1"], text="x" * 600, section_path=[],
                elements=[], element_types=["text"])

    def test_r17b_gate_passes_real_projection(self):
        """真实投影路径通过门禁：正常 chunk 全部 <= 512（含刚好 512）。"""
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        e1 = _text_element("e1", "式中：" + "\n".join([line] * 8))
        doc = _chunk_canonical([_bound_formula("f1", ["e1"], latex="Q=V/A"), e1])
        chunks = _chunk(doc, budget=60)
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)

    def test_r17c_anchor_exactly_512_with_explanation_not_lost(self):
        """刚好 512＋说明：锚点片恰好 512 输出，说明绝不丢失（独立成片）。"""
        header = "[公式 3.1-1]\n"
        body = "Q" * (512 - len(header))
        f = _bound_formula("f1", ["e1"], latex=body)
        e1 = _text_element("e1", "式中：Q——流量。")
        chunks = _chunk(_chunk_canonical([f, e1]))
        fchunks = [c for c in chunks if "f1" in c.element_ids]
        self.assertEqual(len(fchunks), 1)
        # the anchor shard sits EXACTLY at the cap -- complete and untruncated
        self.assertEqual(len(fchunks[0].text), 512)
        self.assertIn(body, fchunks[0].text)
        # no chunk in the whole stream may exceed the hard cap
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)
        # the explanation is NOT absorbed (would exceed) and NOT lost: it
        # keeps its own independent text chunk
        self.assertTrue(any(c.element_ids == ["e1"]
                            and "式中：Q——流量。" in c.text for c in chunks))

    def test_r17d_overflow_shards_respect_hard_cap_everywhere(self):
        """全投影输出（含 locator shard）无一片超过 512."""
        body = "Q=V/A" + "+Q_{rivulets}" * 40
        f = _bound_formula("f1", ["e1"], latex=body)
        e1 = _text_element("e1", "式中：Q——流量。")
        chunks = _chunk(_chunk_canonical([f, e1]))
        for c in chunks:
            self.assertLessEqual(len(c.text), 512)


class MintPreflightTest(unittest.TestCase):
    """候选 mint：缺失哈希或审计输入 -> 零写入硬失败。"""

    def _mk_root(self):
        import tempfile
        return tempfile.mkdtemp(prefix="t09_mint_")

    def _mk_base(self, root, *, pi_sha=True, audit=True, audit_bad=False):
        import hashlib
        base = "corpus-000000000000"
        reg = os.path.join(root, "data", "registry")
        os.makedirs(reg, exist_ok=True)
        pi = os.path.join(root, "data", "canonical", f"{base}-documents.jsonl")
        os.makedirs(os.path.dirname(pi), exist_ok=True)
        with open(pi, "wb") as fh:
            fh.write(b"page-intermediate-bytes\n")
        m = {"corpus_version": base,
             "canonical_documents": f"data/canonical/{base}-documents.jsonl"}
        if pi_sha:
            m["canonical_documents_sha256"] = hashlib.sha256(
                open(pi, "rb").read()).hexdigest()
        if audit:
            ap = os.path.join(root, "data", "canonical",
                              f"{base}-structure-audit.json")
            with open(ap, "wb") as fh:
                fh.write(b"audit-bytes\n")
            m["structure_audit"] = f"data/canonical/{base}-structure-audit.json"
            if not audit_bad:
                m["structure_audit_sha256"] = hashlib.sha256(
                    open(ap, "rb").read()).hexdigest()
        with open(os.path.join(reg, f"{base}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(m, fh)
        return base

    def _run_mint(self, root, base):
        import importlib
        mint = importlib.import_module("scripts.mint_assembly_candidate")
        return mint.main(["--base-corpus-version", base], root=root)

    def _snapshot(self, root):
        out = {}
        for dirpath, _dirs, files in os.walk(root):
            for n in files:
                p = os.path.join(dirpath, n)
                out[p] = os.path.getsize(p)
        return out

    def test_missing_pi_hash_fails_with_zero_writes(self):
        root = self._mk_root()
        base = self._mk_base(root, pi_sha=False)
        before = self._snapshot(root)
        with self.assertRaises(SystemExit):
            self._run_mint(root, base)
        self.assertEqual(self._snapshot(root), before)

    def test_missing_structure_audit_fails_with_zero_writes(self):
        root = self._mk_root()
        base = self._mk_base(root, audit=False)
        before = self._snapshot(root)
        with self.assertRaises(SystemExit):
            self._run_mint(root, base)
        self.assertEqual(self._snapshot(root), before)

    def test_missing_audit_hash_fails_with_zero_writes(self):
        root = self._mk_root()
        base = self._mk_base(root, audit=True, audit_bad=True)
        before = self._snapshot(root)
        with self.assertRaises(SystemExit):
            self._run_mint(root, base)
        self.assertEqual(self._snapshot(root), before)



# --------------------------------------------------------------------------- #
# Closure round 4: identical-projection dedupe (chunk-id uniqueness)
# --------------------------------------------------------------------------- #

# Real sample 48429f37 p22: the OCR promoted the whole 8.4.1 clause to a
# heading, so the table's section prefix alone spans hundreds of characters.
_ROUND4_HEADING = (
    "8.4.1 系统评估工作应由第三方评估单位编制系统评估方案，需对评估范围、评估周期、评估内容等"
    "进行具体说明。 8.4.2 系统评估范围主要是对于未达标的河湖汇水区，在区域性混错接项目实施完成后，"
    "宜以重大雨水排口汇水区（d1000 mm 以上）为单元进行雨水系统评估；宜对污水厂（站）生化需氧量（BOD）浓度"
    "明显偏低的污水系统进行评估。具体范围可结合项目所属区域排水系统的实际情况以及系统评估的情况"
    "予以确定。 8.4.3 系统评估周期宜分为短期评估和长期抽检，短期评估应至少跨越一个完整雨季，"
    "评估应在工程验收和项目评估后进行，评估周期内必须完成相关所有评估内容。长期抽检即在运营期间，"
    "由业主和评估单位共同拟定周期进行评估。 8.4.4 评估内容宜包含下列内容。 "
    "—— 评估监测点的选取：针对所进行系统评估的排水系统对象，结合其雨污水系统特点，合理选取评估监测点。")


def _round4_section_path():
    return [{"label": "9", "title": "运行维护"},
            {"label": "8.4.1", "title": _ROUND4_HEADING}]


def _empty_grid_table(eid, *, rows=38, columns=8, section_path=None):
    """Real-shape empty-grid table (content cells empty -> '| | |' rows)."""
    return {
        "element_id": eid, "type": "table", "role": "table", "label": None,
        "section_path": section_path or [],
        "content": {"row_count": rows, "column_count": columns, "cells": [],
                    "caption": None, "notes": []},
        "source_spans": [{"span_id": "s" + eid, "role": "primary",
                          "physical_page": 1,
                          "bbox": [55.5, 188.5, 524.3, 723.5],
                          "orientation_degrees": 0}],
        "provenance": [], "links": [], "quality_status": "needs_review",
        "policy_reasons": ["structured_default_locator_only"],
        "quality_issues": [], "source_region_ids": [],
        "processing_config_ids": [], "execution_status": "completed",
        "consumed_sources": [], "crop_assets": [], "cross_page_relations": [],
    }


class ClosureRound5OccurrenceIdentityTest(unittest.TestCase):
    """R19 (closure round 5): chunk identity is position-aware.

    R18 deduplicated byte-identical sub-chunks by (element_ids, spans) alone
    -- which silently collapses REAL repeated source occurrences (u6b's 8
    identical explanation lines) and cannot tell a real duplicate row from a
    splitter overlap window. Round 5: every chunk declares the source char
    ranges it carries (metadata ``source_occurrence``, element-relative
    offsets); identical text at DIFFERENT source positions gets
    deterministically unique ids and is retained per occurrence; only a
    re-emission of the SAME id with the SAME projection (spans + policy +
    quality + reasons + occurrence) is dropped; same id with different
    policy/reasons is a HARD failure (never first-wins). Coverage is
    verified by source position / occurrence order, never by text sets.
    """

    def _builder(self, elements, *, budget=480, overlap=64):
        from src.application.v2_chunker import _ChunkBuilder
        doc = _chunk_canonical(elements)
        return _ChunkBuilder(
            canonical=doc, projection_version=FORMULA_BINDING_PROJECTION_VERSION,
            token_budget=budget, overlap=overlap,
            count_fn=_CharTokenizer().count, valid_pages={1}), doc

    # -- e000261 class: real duplicate rows vs overlap windows --------------

    def test_r19a_real_duplicate_rows_retained_per_occurrence(self):
        """38 identical empty-grid rows are 38 REAL source occurrences: the
        chunk stream must cover every row POSITION, and identical window
        texts from different positions keep distinct deterministic ids."""
        from src.application.v2_chunker import _project_text
        doc = _chunk_canonical([_empty_grid_table(
            "e000261", rows=38, section_path=_round4_section_path())])
        chunks = chunk_document(doc, tokenizer=_CharTokenizer())
        ids = [c.chunk_id for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))
        # every chunk declares its carried source ranges
        for c in chunks:
            self.assertTrue(c.metadata.get("source_occurrence"),
                            "chunk must declare source_occurrence ranges")
        el = doc["elements"][0]
        proj = _project_text(el)
        # positional coverage: every non-empty row line's [start, end) must
        # be fully covered by the declared ranges of chunks listing e000261
        lines = []
        off = 0
        for ln in proj.split("\n"):
            if ln.strip():
                lines.append((off, off + len(ln)))
            off += len(ln) + 1
        self.assertEqual(len(lines), 38)
        declared = []
        for c in chunks:
            for o in c.metadata["source_occurrence"]:
                if o["element_id"] == "e000261":
                    declared.append((o["start"], o["end"]))
        for ls, le in lines:
            covered = [d for d in declared if d[0] <= ls and le <= d[1]]
            self.assertGreaterEqual(
                len(covered), 1,
                f"row occurrence at [{ls}, {le}) not covered by any chunk")
        # identical texts at different positions: distinct ids, distinct
        # declared ranges
        by_text = {}
        for c in chunks:
            body = c.text
            by_text.setdefault(body, []).append(c)
        for body, group in by_text.items():
            if len(group) > 1:
                occ = {tuple(sorted((o["start"], o["end"])
                                    for o in c.metadata["source_occurrence"]))
                       for c in group}
                self.assertEqual(len(occ), len(group),
                                 "identical texts must occupy distinct ranges")

    def test_r19b_splitter_overlap_window_never_erases_occurrence(self):
        """Overlap windows re-carry content from positions a neighbouring
        window already covers -- allowed, but the coverage multiplicity of a
        real occurrence must be >= 1 and the union of ranges must equal the
        whole projection (no real occurrence erased, no gap)."""
        from src.application.v2_chunker import _project_text
        doc = _chunk_canonical([_empty_grid_table(
            "e000261", rows=38, section_path=_round4_section_path())])
        chunks = chunk_document(doc, tokenizer=_CharTokenizer())
        el = doc["elements"][0]
        proj = _project_text(el)
        covered = [0] * len(proj)
        for c in chunks:
            for o in c.metadata["source_occurrence"]:
                if o["element_id"] != "e000261":
                    continue
                for i in range(max(0, o["start"]), min(len(proj), o["end"])):
                    covered[i] += 1
        self.assertGreater(min(covered), 0,
                           "gap in positional coverage of the projection")

    # -- u6b class: identical explanation lines retained per occurrence -----

    def test_r19c_identical_explanation_lines_count(self):
        line = "Q——流量，表示单位时间内通过过水断面的流体体积；"
        e1 = _text_element("e1", "式中：" + "\n".join([line] * 8))
        doc = _chunk_canonical([_bound_formula("f1", ["e1"], latex="Q=V/A"), e1])
        chunks = chunk_document(doc, tokenizer=_CharTokenizer(),
                                token_budget=60, overlap=0)
        joined = "\n".join(c.text for c in chunks)
        self.assertEqual(joined.count(line), 8)
        # every shard declares the line range it carries; the 8 occurrences
        # occupy 8 DISTINCT ranges within e1's projection
        ranges = []
        for c in chunks:
            for o in c.metadata["source_occurrence"]:
                if o["element_id"] == "e1":
                    ranges.append((o["start"], o["end"]))
        self.assertEqual(len(ranges), 8)
        self.assertEqual(len(set(ranges)), 8)

    # -- dedupe boundary: policy/reason conflict is a hard failure ----------

    def test_r19d_same_id_different_policy_hard_fails(self):
        """Same id emitted with a DIFFERENT policy projection must hard-fail,
        never silently keep the first emission (first-wins is forbidden)."""
        el_ok = _text_element("e1", "同一文本", quality="passed")
        el_nr = _text_element("e1", "同一文本", quality="needs_review")
        builder, _ = self._builder([el_ok])
        kwargs = dict(element_ids=["e1"], text="同一文本", section_path=[])
        builder._emit_chunk(elements=[el_ok], element_types=["text"], **kwargs)
        with self.assertRaises(ValueError) as ctx:
            builder._emit_chunk(elements=[el_nr], element_types=["text"],
                                **kwargs)
        self.assertIn("policy", str(ctx.exception).lower())

    def test_r19e_same_id_same_projection_dedupes(self):
        el = _text_element("e1", "同一文本")
        builder, _ = self._builder([el])
        kwargs = dict(element_ids=["e1"], text="同一文本", section_path=[],
                      elements=[el], element_types=["text"],
                      occurrence=[{"element_id": "e1", "start": 0, "end": 4}])
        builder._emit_chunk(**kwargs)
        builder._emit_chunk(**kwargs)
        self.assertEqual(len(builder.chunks), 1)
        self.assertEqual(builder.chunks[0].metadata["source_occurrence"],
                         [{"element_id": "e1", "start": 0, "end": 4}])


if __name__ == "__main__":
    unittest.main()
