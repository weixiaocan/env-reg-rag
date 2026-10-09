"""T02 — text & page quality provenance lossless propagation (pre-fix failing tests).

Asserts that native / OCR / blank / quarantine decisions travel losslessly from
the page-intermediate row into the V2 Canonical page/element provenance and
quality_status, and from there into the chunk usage_policy -- without being
lost, fabricated or upgraded by assembly (D2/D4), and without breaking the
closed-schema / digest compatibility of already-published artifacts.

Pre-fix, every assertion below that depends on route-derived provenance or a
persisted ``quality_status`` fails because:

* ``_build_text_element`` hardcodes ``provenance = native_text / native-1`` for
  *every* text element, even on ``full_ocr`` pages (T02 must-complete #2);
* no element carries ``quality_status`` / ``policy_reasons`` and no page carries
  ``extraction_route`` / ``decision_status`` / ``decision_reasons`` (#1);
* so every chunk resolves to ``needs_review`` / ``source_locator_only`` with
  reason ``missing_policy_default_locator_only`` -- readable native text can
  never become answer evidence (regression vs. the T01 answer gate), and
  OCR / quarantine provenance is invisible downstream.

Run::

    PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_t02_provenance_propagation.py -q
"""
from __future__ import annotations

import json
import os
import unittest

from src.application.canonical_assembly import assemble_document
from src.application.canonical_validation import compute_ids, validate_document
from src.application.evidence_policy import (
    ANSWER,
    LOCATOR,
    element_quality_status,
)
from src.application.v2_chunker import chunk_document
from src.domain.canonical_document import (
    SCHEMA_VERSION,
    CanonicalDocument,
    SchemaError,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A real published V2 artifact (built with the pre-T02 assembler). Used for the
# digest-compatibility regression: it must still validate after the schema is
# extended with optional provenance fields.
_PUBLISHED_V2_DIR = os.path.join(
    _REPO_ROOT, "data", "canonical", "v2", "corpus-37456321a968"
)
CECS758_SHA = "f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941"
_PUBLISHED_V2_JSON = os.path.join(_PUBLISHED_V2_DIR, f"{CECS758_SHA}.canonical.json")

# 64-hex dummy sha for synthetic documents.
_DUMMY_SHA = "0" * 64

# Page geometry used by every synthetic page (A4-ish, pt).
_PW, _PH = 595.0, 842.0


def _page(
    pno: int,
    *,
    route: str = "native",
    decision: str = "approved",
    reasons: list[str] | None = None,
    text: str = "",
    elements: list[dict] | None = None,
    regions: list[dict] | None = None,
    rotation: int = 0,
    parser_name: str | None = None,
) -> dict:
    """Build one page-intermediate page row with the T02 provenance fields."""
    if elements is None and text:
        # Default: one full-width text line carrying the page text.
        elements = [{"text": text, "bbox": [60.0, 100.0, 535.0, 140.0]}]
    elif elements is None:
        elements = []
    return {
        "physical_page": pno,
        "page_index": pno - 1,
        "width": _PW,
        "height": _PH,
        "rotation": rotation,
        "display_page_label": str(pno),
        "extraction_route": route,
        "decision_status": decision,
        "decision_reasons": reasons or [],
        "parser_name": parser_name or ("paddleocr-text" if route == "full_ocr" else "pymupdf-native"),
        "parser_profile": "full-corpus-auto-v1",
        "text": text,
        "elements": elements,
        "regions": regions or [],
        "tables": [],
    }


def _text_region(
    bbox: list[float],
    *,
    quality_status: str = "passed",
    issues: list[str] | None = None,
    region_id: str = "up-testregion00000000000000",
    processing_config_id: str = "unified-local-v5:cfg",
) -> dict:
    """Build one upstream text region (page-intermediate ``regions[]`` shape)."""
    return {
        "region_id": region_id,
        "element_id": region_id,
        "kind": "text",
        "quality_status": quality_status,
        "execution_status": "completed",
        "issues": issues or [],
        "processing_config_id": processing_config_id,
        "bbox": bbox,
        "text": "",
    }


def _doc(pages: list[dict], *, sha: str = _DUMMY_SHA) -> dict:
    return {
        "sha256": sha,
        "file_name": "synthetic.pdf",
        "config_hash": "a" * 64,
        "processing_run_id": "run_test",
        "metadata": {"document_kind": "standard"},
        "pages": pages,
    }


def _assemble(page_doc: dict, *, formula_pages=None) -> dict:
    doc = assemble_document(page_doc, formula_pages=formula_pages or {})
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
    return doc


def _text_elements_on(doc: dict, page: int) -> list[dict]:
    out = []
    for e in doc["elements"]:
        if e["type"] != "text":
            continue
        if any(s["physical_page"] == page for s in e["source_spans"]):
            out.append(e)
    return out


def _all_elements_on(doc: dict, page: int) -> list[dict]:
    out = []
    for e in doc["elements"]:
        if any(s["physical_page"] == page for s in e["source_spans"]):
            out.append(e)
    return out


class _CharTokenizer:
    """Tokenizer whose count == len(text); mirrors tests/test_v2_chunker.py."""

    def count(self, text: str) -> int:  # noqa: D401
        return len(text)

    def encode(self, text: str):  # noqa: D401
        return text.encode("utf-8")


def _chunks(doc: dict) -> list:
    return chunk_document(doc, token_budget=480, overlap=0, tokenizer=_CharTokenizer())


# --------------------------------------------------------------------------- #
# 1. Native readable text -> passed + answer, native_text provenance
# --------------------------------------------------------------------------- #
class TestNativeReadableText(unittest.TestCase):
    def test_native_readable_text_passed_and_answer(self):
        page_doc = _doc([_page(1, route="native", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        validate_document(doc)
        els = _text_elements_on(doc, 1)
        self.assertEqual(len(els), 1)
        el = els[0]
        # Provenance must record the native route (not fabricated OCR).
        methods = [p["method"] for p in el["provenance"]]
        self.assertIn("native_text", methods)
        self.assertNotIn("ocr", methods)
        # Engine resolves into generation.engines (invariant 16).
        engine_ids = {eng["engine_id"] for eng in doc["generation"]["engines"]}
        for p in el["provenance"]:
            self.assertIn(p["engine_id"], engine_ids)
        # Native + approved + answerable -> passed (explicit, not fabricated).
        self.assertEqual(el.get("quality_status"), "passed")
        # Chunk projects to answer evidence.
        chunks = _chunks(doc)
        self.assertTrue(any(c.metadata["usage_policy"] == ANSWER for c in chunks),
                        f"expected an answer chunk, got {[c.metadata['usage_policy'] for c in chunks]}")


# --------------------------------------------------------------------------- #
# 2. Native garbled -> OCR route: OCR text must NOT be native_text
# --------------------------------------------------------------------------- #
class TestNativeGarbledTriggersOcr(unittest.TestCase):
    def test_ocr_text_not_native_text(self):
        # Native failed (mojibake) -> pipeline re-ran OCR; page-intermediate
        # records the OCR outcome (route=full_ocr, approved, readable Chinese).
        page_doc = _doc([_page(1, route="full_ocr", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        validate_document(doc)
        el = _text_elements_on(doc, 1)[0]
        methods = [p["method"] for p in el["provenance"]]
        self.assertIn("ocr", methods)
        self.assertNotIn("native_text", methods,
                         "OCR text must not be labelled native_text (T02 #2)")
        # OCR engine registered.
        engine_ids = {eng["engine_id"] for eng in doc["generation"]["engines"]}
        self.assertIn("ocr-1", engine_ids)
        for p in el["provenance"]:
            self.assertIn(p["engine_id"], engine_ids)


# --------------------------------------------------------------------------- #
# 3. OCR success -> needs_review (not passed), locator-only
# --------------------------------------------------------------------------- #
class TestOcrSuccessNeedsReview(unittest.TestCase):
    def test_ocr_success_needs_review_locator(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "needs_review")
        chunks = _chunks(doc)
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks),
                        "OCR text must not support an answer without human review")


# --------------------------------------------------------------------------- #
# 4. OCR still unreadable -> locator-only + unreadable reason
# --------------------------------------------------------------------------- #
class TestOcrStillUnreadable(unittest.TestCase):
    def test_ocr_unreadable_locator(self):
        # OCR ran but the text is still mojibake-ish (han<0.12, symbol>=0.25).
        garbled = "!\"# !\"# !$! $ %\" '()*+,-./0123 23 4 5 %&'!(!)' (!)'*!'*)& 56"
        page_doc = _doc([_page(1, route="full_ocr", decision="approved", text=garbled)])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertIn(el.get("quality_status"), {"needs_review", "quarantined"})
        chunks = _chunks(doc)
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks))
        # Chunk reason codes record the unreadable text floor.
        all_reasons = [r for c in chunks for r in (c.metadata.get("policy_reasons") or [])]
        self.assertTrue(all_reasons, "expected at least one policy reason code")


# --------------------------------------------------------------------------- #
# 5. Blank page: page record kept, no element / no chunk
# --------------------------------------------------------------------------- #
class TestBlankPage(unittest.TestCase):
    def test_blank_page_no_element(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="quarantine",
                               reasons=["visual_blank_page"], text="", elements=[])])
        doc = _assemble(page_doc)
        validate_document(doc)
        # Page record is retained (slot preserved)...
        self.assertEqual(len(doc["pages"]), 1)
        self.assertEqual(doc["pages"][0]["parse_status"], "blank")
        # ...but it contributes no element.
        self.assertEqual(_all_elements_on(doc, 1), [])
        # ...and no chunk.
        self.assertEqual(_chunks(doc), [])


# --------------------------------------------------------------------------- #
# 6. Quarantine page with text: locator-only, never answer
# --------------------------------------------------------------------------- #
class TestQuarantinePageLocator(unittest.TestCase):
    def test_quarantine_text_locator_only(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="quarantine",
                               reasons=["low_confidence_text"],
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "quarantined")
        chunks = _chunks(doc)
        self.assertTrue(chunks)
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks),
                        "quarantine text must not support an answer (#4)")


# --------------------------------------------------------------------------- #
# 7. Low-han pages under the T08-amended readability gate.
#    T08 review root cause (2): a bare low han_ratio is NOT evidence of
#    corruption -- a native English contents page is a faithful text layer,
#    so it is approved/passed.  Corruption evidence (replacement chars,
#    symbol mass) must still keep the page off `passed` (the original #5
#    concern: unreadable content is never faked as readable).
# --------------------------------------------------------------------------- #
class TestEnglishTocNotPassed(unittest.TestCase):
    def test_english_toc_is_passed_under_amended_gate(self):
        # T08: native English TOC pages (e.g. 4336797d p8) are genuinely
        # readable; the old bare low_han_ratio quarantine was a false
        # positive and the element may be `passed` when native+approved.
        toc = ("Contents 1 General provisions 2 Terms and symbols "
               "3 Basic provisions 4 Design requirements 5 Construction")
        page_doc = _doc([_page(1, route="native", decision="approved", text=toc)])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "passed",
                         "faithful native English TOC is answerable (T08 fix 1)")
        chunks = _chunks(doc)
        self.assertTrue(all(c.metadata["usage_policy"] == ANSWER for c in chunks))

    def test_corrupt_low_han_text_still_not_passed(self):
        # #5 preserved: corruption evidence (replacement characters) keeps
        # low-han text off `passed` even when the page was approved.
        corrupt = ("Contents 1 General provisions 2 Terms and symbols "
                   "3 Basic provisions " + "�" * 40)
        page_doc = _doc([_page(1, route="native", decision="approved", text=corrupt)])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed",
                            "corrupt low-han text must never be faked as passed (#5)")
        self.assertEqual(el.get("quality_status"), "needs_review")


# --------------------------------------------------------------------------- #
# 8. Short-title page: too_short -> needs_review, not passed
# --------------------------------------------------------------------------- #
class TestShortTitleNotPassed(unittest.TestCase):
    def test_short_title_needs_review(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="approved",
                               text="重庆工程建设")])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        chunks = _chunks(doc)
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks))


# --------------------------------------------------------------------------- #
# 9. Pure-number page: not passed
# --------------------------------------------------------------------------- #
class TestPureNumberPageNotPassed(unittest.TestCase):
    def test_pure_number_not_passed(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="approved",
                               text="1511244269 33.00 15112 44269 33 00")])
        doc = _assemble(page_doc)
        el = _text_elements_on(doc, 1)
        if el:  # may be consumed/dropped; if present it must not be passed
            self.assertNotEqual(el[0].get("quality_status"), "passed")
        chunks = _chunks(doc)
        self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks))


# --------------------------------------------------------------------------- #
# 10. Formula-dominant page: structured element defaults locator-only (D3)
# --------------------------------------------------------------------------- #
class TestFormulaPageLocator(unittest.TestCase):
    def test_formula_element_locator_only(self):
        # T04: the page-intermediate formula region is the DETECTION ground
        # truth; the cache supplies the recognition result for that region.
        bbox = [100.0, 200.0, 400.0, 260.0]
        page = _page(1, route="full_ocr", decision="approved",
                     text="式中参数取值如下表所示。",
                     elements=[{"text": "式中参数取值如下表所示。",
                                "bbox": [60.0, 100.0, 535.0, 140.0]}],
                     regions=[{
                         "region_id": "up-formula00000000000001",
                         "kind": "formula", "bbox": bbox,
                         "execution_status": "completed",
                         "quality_status": "needs_review", "issues": [],
                         "relations": [], "physical_page": 1,
                         "processing_config_id": "unified-local-v5:cfg",
                         "raw_latex": "T = \\sum p_j",
                     }])
        page_doc = _doc([page])
        formula_pages = {
            1: [{"kind": "formula", "score": 0.9, "bbox": bbox,
                 "raw_latex": "T = \\sum p_j"}]
        }
        doc = _assemble(page_doc, formula_pages=formula_pages)
        formulas = [e for e in doc["elements"] if e["type"] == "formula"]
        self.assertTrue(formulas, "expected a formula element")
        f = formulas[0]
        # Structured content defaults to needs_review (D3) without a review ID.
        self.assertEqual(f.get("quality_status"), "needs_review")
        chunks = _chunks(doc)
        formula_chunks = [c for c in chunks if "formula" in c.metadata.get("element_types", [])]
        if formula_chunks:
            self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in formula_chunks),
                            "formula chunk must default to locator-only (D3)")


# --------------------------------------------------------------------------- #
# 11. Rotated page: rotation + route carried into the Canonical page
# --------------------------------------------------------------------------- #
class TestRotatedPage(unittest.TestCase):
    def test_rotation_and_route_carried(self):
        page_doc = _doc([_page(1, route="full_ocr", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。",
                               rotation=270)])
        doc = _assemble(page_doc)
        validate_document(doc)
        p = doc["pages"][0]
        self.assertEqual(p["rotation_degrees"], 270)
        self.assertEqual(p.get("extraction_route"), "full_ocr")
        el = _text_elements_on(doc, 1)[0]
        methods = [pr["method"] for pr in el["provenance"]]
        self.assertIn("ocr", methods)


# --------------------------------------------------------------------------- #
# 11b. OCR confidence + engine version flow through to element provenance
# --------------------------------------------------------------------------- #
class TestOcrConfidenceFlow(unittest.TestCase):
    def test_ocr_confidence_and_engine_version(self):
        page = _page(1, route="full_ocr", decision="approved",
                     text="本标准规定了城镇内涝防治的技术要求。")
        page["ocr_confidence"] = 0.91
        page["engine_version"] = "paddleocr-3.7.0"
        page["parser_name"] = "paddleocr-text"
        doc = _assemble(_doc([page]))
        validate_document(doc)
        el = _text_elements_on(doc, 1)[0]
        prov = el["provenance"][0]
        self.assertEqual(prov["method"], "ocr")
        self.assertEqual(prov["engine_id"], "ocr-1")
        self.assertAlmostEqual(prov["confidence"], 0.91, places=4)
        # Engine registered with the real name/version.
        ocr_eng = next(e for e in doc["generation"]["engines"] if e["engine_id"] == "ocr-1")
        self.assertEqual(ocr_eng["name"], "paddleocr-text")
        self.assertEqual(ocr_eng["version"], "paddleocr-3.7.0")


# --------------------------------------------------------------------------- #
# 12. Page-level provenance fields persisted
# --------------------------------------------------------------------------- #
class TestPageProvenanceFields(unittest.TestCase):
    def test_page_carries_route_and_decision(self):
        page_doc = _doc([
            _page(1, route="native", decision="approved", text="本标准规定了城镇内涝防治的技术要求。"),
            _page(2, route="full_ocr", decision="quarantine", reasons=["low_confidence_text"],
                  text="低置信度文字内容示例。"),
            _page(3, route="full_ocr", decision="quarantine", reasons=["visual_blank_page"],
                  text="", elements=[]),
        ])
        doc = _assemble(page_doc)
        validate_document(doc)
        p1, p2, p3 = doc["pages"]
        self.assertEqual(p1["parse_status"], "parsed")
        self.assertEqual(p1["extraction_route"], "native")
        self.assertEqual(p1["decision_status"], "approved")
        self.assertEqual(p2["parse_status"], "parsed")  # quarantine+text keeps the slot
        self.assertEqual(p2["extraction_route"], "full_ocr")
        self.assertEqual(p2["decision_status"], "quarantine")
        self.assertIn("low_confidence_text", p2["decision_reasons"])
        self.assertEqual(p3["parse_status"], "blank")
        self.assertEqual(p3["decision_status"], "quarantine")


# --------------------------------------------------------------------------- #
# 13. Digest compatibility: published pre-T02 artifact still validates
# --------------------------------------------------------------------------- #
class TestDigestCompatibility(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(_PUBLISHED_V2_JSON),
                         "published V2 artifact not present; cannot run compat regression")
    def test_published_artifact_validates(self):
        with open(_PUBLISHED_V2_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        # The pre-T02 artifact has no quality_status / extraction_route fields.
        # After the schema is extended with optional digest-excluded fields it
        # MUST still validate (digests recomputed from the shared content fields).
        validate_document(doc)

    def test_quality_status_excluded_from_content_digest(self):
        # Two documents identical except for element quality_status must have the
        # SAME canonical_content_id: quality/provenance fields are metadata about
        # extraction, not content identity, so they are excluded from the digest.
        base = _doc([_page(1, route="native", decision="approved",
                           text="本标准规定了城镇内涝防治的技术要求。")])
        d1 = _assemble(base)
        # Hand-craft a second doc whose only difference is quality_status.
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["quality_status"] = "quarantined"
        d2["elements"][0]["policy_reasons"] = ["manual_override"]
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])

    def test_extraction_route_excluded_from_content_digest(self):
        base = _doc([_page(1, route="native", decision="approved",
                           text="本标准规定了城镇内涝防治的技术要求。")])
        d1 = _assemble(base)
        d2 = json.loads(json.dumps(d1))
        d2["pages"][0]["extraction_route"] = "full_ocr"
        d2["pages"][0]["decision_status"] = "quarantine"
        d2["pages"][0]["decision_reasons"] = ["low_confidence_text"]
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])


# --------------------------------------------------------------------------- #
# 14. Closed schema: optional fields accepted; unknown fields still rejected
# --------------------------------------------------------------------------- #
class TestClosedSchemaOptionalFields(unittest.TestCase):
    def test_missing_quality_status_accepted(self):
        page_doc = _doc([_page(1, route="native", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        el = doc["elements"][0]
        # Drop the provenance/quality fields to mimic a legacy element dict.
        el.pop("quality_status", None)
        el.pop("policy_reasons", None)
        # Must round-trip through the closed schema without raising.
        model = CanonicalDocument.from_dict(doc)
        self.assertEqual(model.schema_version, SCHEMA_VERSION)

    def test_unknown_element_field_rejected(self):
        page_doc = _doc([_page(1, route="native", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        doc["elements"][0]["bogus_field"] = 1
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_unknown_page_field_rejected(self):
        page_doc = _doc([_page(1, route="native", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        doc["pages"][0]["bogus_field"] = 1
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_quality_status_value_closed(self):
        page_doc = _doc([_page(1, route="native", decision="approved",
                               text="本标准规定了城镇内涝防治的技术要求。")])
        doc = _assemble(page_doc)
        doc["elements"][0]["quality_status"] = "totally_fine"  # not in vocabulary
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)


# --------------------------------------------------------------------------- #
# 15. T01 evidence_policy still treats a missing quality as needs_review
# --------------------------------------------------------------------------- #
class TestT01PolicyCompat(unittest.TestCase):
    def test_missing_quality_defaults_needs_review(self):
        el = {"type": "text"}  # no quality_status field
        self.assertEqual(element_quality_status(el), "needs_review")
        # And a structured element with no review ID stays locator-only (D3).
        from src.application.evidence_policy import element_usage_policy
        self.assertEqual(element_usage_policy({"type": "table", "quality_status": "passed"}),
                         LOCATOR)


# ========================================================================== #
# T02 FINAL CLOSURE — full state matrix
#
# Covers the 9 dimensions the reviewer enumerated. Every assertion encodes the
# TARGET (post-fix) behaviour, so on the pre-fix implementation these expose
# every boundary failure at once:
#   * missing/unknown route or decision promoted to native+approved -> passed
#   * multi-region overlap picks the first (loosest) region, order-dependent
#   * single source_region_id cannot express one-to-many region participation
#   * engine_id referenced in audit without its name/version -> engine version
#     change does not flip canonical_id (R5 violation)
# ========================================================================== #

_READABLE = "本标准规定了城镇内涝防治的技术要求。"
_EBBOX = [60.0, 100.0, 535.0, 140.0]


def _elem(text=_READABLE, bbox=_EBBOX):
    return {"text": text, "bbox": list(bbox)}


def _page_no_route(pno=1, *, text=_READABLE, decision="approved", elements=None):
    """A page-intermediate row whose extraction_route field is ABSENT."""
    p = _page(pno, route="native", decision=decision, text=text, elements=elements)
    p.pop("extraction_route", None)
    return p


def _page_no_decision(pno=1, *, text=_READABLE, route="native", elements=None):
    p = _page(pno, route=route, decision="approved", text=text, elements=elements)
    p.pop("decision_status", None)
    return p


def _methods(doc, page=1):
    el = _text_elements_on(doc, page)[0]
    return [p["method"] for p in el["provenance"]]


def _no_answer(doc):
    chunks = _chunks(doc)
    return all(c.metadata["usage_policy"] != ANSWER for c in chunks)


# --------------------------------------------------------------------------- #
# 16. route x decision -> element quality + provenance method + chunk policy
# --------------------------------------------------------------------------- #
class TestRouteDecisionMatrix(unittest.TestCase):
    def test_native_approved_readable_passed_answer(self):
        doc = _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "passed")
        self.assertIn("native_text", _methods(doc))
        self.assertTrue(any(c.metadata["usage_policy"] == ANSWER for c in _chunks(doc)))

    def test_native_approved_unreadable_needs_review(self):
        garbled = "!\"# !\"# !$! $ %\" '()*+,-./0123 23 4 5 %&'!(!)' (!)'*!'*)& 56"
        doc = _assemble(_doc([_page(1, route="native", decision="approved", text=garbled)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        self.assertTrue(_no_answer(doc))

    def test_full_ocr_approved_readable_needs_review_ocr(self):
        doc = _assemble(_doc([_page(1, route="full_ocr", decision="approved", text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "needs_review")
        self.assertIn("ocr", _methods(doc))
        self.assertNotIn("native_text", _methods(doc))
        self.assertTrue(_no_answer(doc))

    def test_full_ocr_quarantine_quarantined(self):
        doc = _assemble(_doc([_page(1, route="full_ocr", decision="quarantine",
                                     reasons=["low_confidence_text"], text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "quarantined")
        self.assertTrue(_no_answer(doc))

    def test_hybrid_approved_needs_review_hybrid(self):
        doc = _assemble(_doc([_page(1, route="hybrid", decision="approved", text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "needs_review")
        self.assertIn("hybrid", _methods(doc))
        self.assertTrue(_no_answer(doc))

    def test_failed_route_no_element(self):
        # route=failed -> parse_status=failed -> no slots, no elements, no chunks.
        doc = _assemble(_doc([_page(1, route="failed", decision="failed", text=_READABLE)]))
        self.assertEqual(_text_elements_on(doc, 1), [])
        self.assertEqual(_chunks(doc), [])

    # -- missing / unknown route or decision (P1a core) --------------------- #
    def test_missing_route_not_native_not_passed(self):
        doc = _assemble(_doc([_page_no_route(1, text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed",
                            "missing route must not be promoted to passed")
        self.assertNotIn("native_text", _methods(doc),
                         "missing route must not be labelled native_text")
        self.assertTrue(_no_answer(doc))

    def test_unknown_route_not_native_not_passed(self):
        p = _page(1, route="garbage_route", decision="approved", text=_READABLE)
        doc = _assemble(_doc([p]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        self.assertNotIn("native_text", _methods(doc))
        self.assertTrue(_no_answer(doc))

    def test_missing_decision_not_passed(self):
        doc = _assemble(_doc([_page_no_decision(1, route="native", text=_READABLE)]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed",
                            "missing decision must not be promoted to approved/passed")
        self.assertTrue(_no_answer(doc))

    def test_unknown_decision_not_passed(self):
        p = _page(1, route="native", decision="garbage_decision", text=_READABLE)
        doc = _assemble(_doc([p]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        self.assertTrue(_no_answer(doc))

    def test_missing_route_missing_decision_not_passed(self):
        p = _page_no_route(1, text=_READABLE)
        p.pop("decision_status", None)
        doc = _assemble(_doc([p]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        self.assertTrue(_no_answer(doc))


# --------------------------------------------------------------------------- #
# 17. region: single / multi-overlap / order-swap / missing / unknown
# --------------------------------------------------------------------------- #
class TestRegionMatrix(unittest.TestCase):
    def test_single_passed_region_passed(self):
        region = _text_region(_EBBOX, quality_status="passed", region_id="up-rA00000000000000001")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[region])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "passed")

    def test_single_quarantined_region_quarantined(self):
        region = _text_region(_EBBOX, quality_status="quarantined",
                              issues=["visual_only_page"], region_id="up-rQ00000000000000001")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[region])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "quarantined")
        self.assertTrue(_no_answer(doc))

    def test_passed_plus_quarantined_overlap_strictest_quarantined(self):
        # Two regions both fully contain the element: one passed, one quarantined.
        r_pass = _text_region(_EBBOX, quality_status="passed", region_id="up-rP00000000000000001")
        r_quar = _text_region(_EBBOX, quality_status="quarantined",
                              issues=["low_confidence_text"], region_id="up-rQ00000000000000002")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[r_pass, r_quar])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "quarantined",
                         "strictest(passed, quarantined) must be quarantined regardless of order")
        self.assertTrue(_no_answer(doc))

    def test_overlap_order_swap_same_result(self):
        # Same regions as above but reversed input order -> identical quality.
        r_quar = _text_region(_EBBOX, quality_status="quarantined",
                              issues=["low_confidence_text"], region_id="up-rQ00000000000000002")
        r_pass = _text_region(_EBBOX, quality_status="passed", region_id="up-rP00000000000000001")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[r_quar, r_pass])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "quarantined",
                         "region order must not affect the strictest result")

    def test_two_containments_both_passed_passed(self):
        r1 = _text_region(_EBBOX, quality_status="passed", region_id="up-rP00000000000000001")
        r2 = _text_region(_EBBOX, quality_status="passed", region_id="up-rP00000000000000002")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[r1, r2])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertEqual(el.get("quality_status"), "passed")

    def test_all_participating_region_ids_preserved(self):
        r1 = _text_region(_EBBOX, quality_status="passed", region_id="up-rP00000000000000001",
                          processing_config_id="cfg-A", issues=["i_a"])
        r2 = _text_region(_EBBOX, quality_status="quarantined", region_id="up-rQ00000000000000002",
                          processing_config_id="cfg-B", issues=["i_b", "low_confidence_text"])
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[r1, r2])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        ids = el.get("source_region_ids") or []
        self.assertIn("up-rP00000000000000001", ids)
        self.assertIn("up-rQ00000000000000002", ids)
        self.assertEqual(len(ids), 2, "both participating region IDs must be preserved")
        cfgs = el.get("processing_config_ids") or []
        self.assertIn("cfg-A", cfgs)
        self.assertIn("cfg-B", cfgs)
        issues = el.get("quality_issues") or []
        self.assertIn("low_confidence_text", issues)

    def test_unmatched_region_not_passed(self):
        far = _text_region([10.0, 700.0, 50.0, 720.0], quality_status="passed",
                           region_id="up-rF00000000000000001")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[far])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed")
        self.assertEqual(el.get("source_region_ids") or [], [])
        self.assertTrue(_no_answer(doc))

    def test_region_unknown_quality_not_passed(self):
        region = _text_region(_EBBOX, quality_status="garbage_quality",
                              region_id="up-rU00000000000000001")
        page = _page(1, route="native", decision="approved", text=_READABLE,
                     elements=[_elem()], regions=[region])
        doc = _assemble(_doc([page]))
        el = _text_elements_on(doc, 1)[0]
        self.assertNotEqual(el.get("quality_status"), "passed",
                            "unknown region quality must not be promoted to passed")
        self.assertTrue(_no_answer(doc))


# --------------------------------------------------------------------------- #
# 18. text floor x page shape
# --------------------------------------------------------------------------- #
class TestTextFloorAndPageShape(unittest.TestCase):
    def test_empty_text_no_element(self):
        page = _page(1, route="native", decision="approved", text="", elements=[])
        doc = _assemble(_doc([page]))
        self.assertEqual(_text_elements_on(doc, 1), [])
        self.assertEqual(_chunks(doc), [])

    def test_rotated_page_carries_route(self):
        page = _page(1, route="full_ocr", decision="approved", text=_READABLE, rotation=270)
        doc = _assemble(_doc([page]))
        validate_document(doc)
        self.assertEqual(doc["pages"][0]["rotation_degrees"], 270)
        self.assertEqual(doc["pages"][0]["extraction_route"], "full_ocr")
        self.assertIn("ocr", _methods(doc))

    def test_blank_page_no_element(self):
        page = _page(1, route="full_ocr", decision="quarantine",
                     reasons=["visual_blank_page"], text="", elements=[])
        doc = _assemble(_doc([page]))
        validate_document(doc)
        self.assertEqual(doc["pages"][0]["parse_status"], "blank")
        self.assertEqual(_all_elements_on(doc, 1), [])
        self.assertEqual(_chunks(doc), [])


# --------------------------------------------------------------------------- #
# 19. engine: single / multi / missing name / missing version
# --------------------------------------------------------------------------- #
class TestEngineMatrix(unittest.TestCase):
    def test_distinct_ocr_versions_registered_separately(self):
        p1 = _page(1, route="full_ocr", decision="approved", text=_READABLE)
        p1["parser_name"] = "paddleocr-text"; p1["engine_version"] = "3.7.0"
        p2 = _page(2, route="full_ocr", decision="approved", text="术语和符号如下所示。")
        p2["parser_name"] = "paddleocr-text"; p2["engine_version"] = "3.8.0"
        doc = _assemble(_doc([p1, p2]))
        validate_document(doc)
        ocr = [e for e in doc["generation"]["engines"] if e["engine_id"].startswith("ocr-")]
        self.assertEqual(len(ocr), 2)
        self.assertEqual({e["version"] for e in ocr}, {"3.7.0", "3.8.0"})
        self.assertNotEqual(doc["pages"][0]["engine_id"], doc["pages"][1]["engine_id"])

    def test_missing_engine_version_is_unknown(self):
        p1 = _page(1, route="full_ocr", decision="approved", text=_READABLE)
        p1.pop("parser_name", None)
        doc = _assemble(_doc([p1]))
        validate_document(doc)
        ocr = next(e for e in doc["generation"]["engines"] if e["engine_id"] == "ocr-1")
        self.assertEqual(ocr["version"], "unknown")

    def test_missing_engine_name_defaults(self):
        p1 = _page(1, route="full_ocr", decision="approved", text=_READABLE)
        p1["engine_version"] = "3.7.0"
        p1.pop("parser_name", None)
        doc = _assemble(_doc([p1]))
        validate_document(doc)
        ocr = next(e for e in doc["generation"]["engines"] if e["engine_id"] == "ocr-1")
        self.assertEqual(ocr["name"], "paddleocr-text")

    def test_blank_ocr_page_retains_engine_config_confidence(self):
        blank = _page(2, route="full_ocr", decision="quarantine",
                      reasons=["visual_blank_page"], text="", elements=[])
        blank["ocr_confidence"] = 0.42
        blank["engine_version"] = "paddleocr-3.7.0"
        blank["parser_name"] = "paddleocr-text"
        blank["layout_config_hash"] = "b" * 64
        doc = _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE), blank]))
        validate_document(doc)
        bp = doc["pages"][1]
        self.assertEqual(bp["parse_status"], "blank")
        self.assertEqual(bp["engine_id"], "ocr-1")
        self.assertAlmostEqual(bp["confidence"], 0.42, places=4)
        self.assertEqual(bp["config_hash"], "b" * 64)
        self.assertEqual(_all_elements_on(doc, 2), [])
        eids = {e["engine_id"] for e in doc["generation"]["engines"]}
        self.assertIn(bp["engine_id"], eids)

    def test_blank_page_confidence_out_of_range_rejected(self):
        blank = _page(2, route="full_ocr", decision="quarantine",
                      reasons=["visual_blank_page"], text="", elements=[])
        blank["ocr_confidence"] = 1.5
        with self.assertRaises(SchemaError):
            _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE), blank]))


# --------------------------------------------------------------------------- #
# 20. identity: content / quality / decision / engine-version / config changes
# --------------------------------------------------------------------------- #
class TestAuditIdentityMatrix(unittest.TestCase):
    def test_content_change_flips_both_ids(self):
        d1 = _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE)]))
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["content"]["text"] = "本标准规定了城镇雨水排水的设计要求。"
        ids2 = compute_ids(d2)
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])
        self.assertNotEqual(d1["canonical_content_id"], ids2["canonical_content_id"])

    def test_quality_change_flips_canonical_id_not_content_id(self):
        d1 = _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE)]))
        d2 = json.loads(json.dumps(d1))
        d2["elements"][0]["quality_status"] = "quarantined"
        d2["elements"][0]["policy_reasons"] = ["manual_quarantine"]
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_page_decision_change_flips_canonical_id(self):
        d1 = _assemble(_doc([_page(1, route="native", decision="approved", text=_READABLE)]))
        d2 = json.loads(json.dumps(d1))
        d2["pages"][0]["decision_status"] = "quarantine"
        d2["pages"][0]["decision_reasons"] = ["low_confidence_text"]
        ids2 = compute_ids(d2)
        self.assertEqual(d1["canonical_content_id"], ids2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], ids2["canonical_id"])

    def test_engine_version_change_flips_canonical_id(self):
        # Two SEPARATE candidates with the same engine_id "ocr-1" but different
        # underlying engine versions. canonical_id MUST differ (R5); content_id
        # may stay equal (same body text).
        def _build(version):
            p = _page(1, route="full_ocr", decision="approved", text=_READABLE)
            p["parser_name"] = "paddleocr-text"; p["engine_version"] = version
            return _assemble(_doc([p]))
        d_old = _build("3.6.0")
        d_new = _build("3.7.0")
        self.assertEqual(d_old["canonical_content_id"], d_new["canonical_content_id"],
                         "same body -> same content_id")
        self.assertNotEqual(d_old["canonical_id"], d_new["canonical_id"],
                            "engine version change must flip the audit identity (R5)")

    def test_config_hash_change_flips_canonical_id(self):
        d1 = _assemble(_doc([_page(1, route="full_ocr", decision="approved", text=_READABLE,
                                   regions=[_text_region(_EBBOX, quality_status="passed")])]))
        d1["pages"][0]["layout_config_hash"] = "a" * 64
        # Rebuild so config_hash lands on the page envelope.
        p1 = _page(1, route="full_ocr", decision="approved", text=_READABLE)
        p1["layout_config_hash"] = "a" * 64
        d1 = _assemble(_doc([p1]))
        p2 = _page(1, route="full_ocr", decision="approved", text=_READABLE)
        p2["layout_config_hash"] = "b" * 64
        d2 = _assemble(_doc([p2]))
        self.assertEqual(d1["canonical_content_id"], d2["canonical_content_id"])
        self.assertNotEqual(d1["canonical_id"], d2["canonical_id"],
                            "config_hash change must flip the audit identity")

    @unittest.skipUnless(os.path.isfile(_PUBLISHED_V2_JSON),
                         "published V2 artifact not present")
    def test_published_artifact_canonical_id_unchanged(self):
        with open(_PUBLISHED_V2_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        stored = doc["canonical_id"]
        ids = compute_ids(doc)
        self.assertEqual(stored, ids["canonical_id"],
                         "legacy artifact canonical_id must be unchanged")

    @unittest.skipUnless(os.path.isfile(_PUBLISHED_V2_JSON),
                         "published V2 artifact not present")
    def test_published_artifact_still_validates(self):
        with open(_PUBLISHED_V2_JSON, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_document(doc)


# --------------------------------------------------------------------------- #
# 21. projection: no combination may promote missing/unknown/nr/quarantined to answer
# --------------------------------------------------------------------------- #
class TestProjectionNeverAnswer(unittest.TestCase):
    """Every conservative input must project to a non-answer chunk policy."""
    def _assert_no_answer(self, doc, msg):
        chunks = _chunks(doc)
        if chunks:
            self.assertTrue(all(c.metadata["usage_policy"] != ANSWER for c in chunks), msg)

    def test_missing_route_no_answer(self):
        self._assert_no_answer(_assemble(_doc([_page_no_route(1)])), "missing route")

    def test_unknown_route_no_answer(self):
        self._assert_no_answer(_assemble(_doc([_page(1, route="zzz", decision="approved", text=_READABLE)])), "unknown route")

    def test_missing_decision_no_answer(self):
        self._assert_no_answer(_assemble(_doc([_page_no_decision(1)])), "missing decision")

    def test_unknown_decision_no_answer(self):
        self._assert_no_answer(_assemble(_doc([_page(1, route="native", decision="zzz", text=_READABLE)])), "unknown decision")

    def test_quarantine_no_answer(self):
        self._assert_no_answer(_assemble(_doc([_page(1, route="full_ocr", decision="quarantine",
                                                     reasons=["low_confidence_text"], text=_READABLE)])), "quarantine")

    def test_needs_review_region_no_answer(self):
        r = _text_region(_EBBOX, quality_status="needs_review", region_id="up-rN00000000000000001")
        self._assert_no_answer(_assemble(_doc([_page(1, route="native", decision="approved",
                                                       text=_READABLE, elements=[_elem()], regions=[r])])),
                               "needs_review region")

    def test_quarantined_region_no_answer(self):
        r = _text_region(_EBBOX, quality_status="quarantined", region_id="up-rQ00000000000000001")
        self._assert_no_answer(_assemble(_doc([_page(1, route="native", decision="approved",
                                                       text=_READABLE, elements=[_elem()], regions=[r])])),
                               "quarantined region")

    def test_unmatched_region_no_answer(self):
        far = _text_region([10.0, 700.0, 50.0, 720.0], quality_status="passed", region_id="up-rF00000000000000001")
        self._assert_no_answer(_assemble(_doc([_page(1, route="native", decision="approved",
                                                       text=_READABLE, elements=[_elem()], regions=[far])])),
                               "unmatched region")


if __name__ == "__main__":
    unittest.main()
