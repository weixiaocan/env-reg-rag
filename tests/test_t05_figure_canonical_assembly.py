"""T05 -- faithful figure / image Canonical assembly.

Every DETECTED image region (page-intermediate ground truth) must close with
exactly ONE outcome and, when the real crop asset verifies, with exactly one
FigureElement. The contract (mirrors the frozen T03/T04 outcome-ledger
patterns, kind="figure"):

* NO fabricated content: the old ``_placeholder_asset`` (bbox-derived ref +
  sha-of-ref-string + pt->px guessed pixel size) is gone. Only the PI's real
  ``image_ref`` / ``image_sha256`` is used, and the file itself is verified
  (exists, digest matches, true pixel size read from the image header).
* Caption / figure_number come ONLY from the PI structured fields; an upstream
  ``caption_not_found`` stays a missing caption -- nearby "图N" text is never
  regex-guessed into a caption.
* References / relations come ONLY from explicit PI relations (verbatim kind,
  basis, verified, source page/bbox/text); body text is never scanned for
  references.
* FigureElements carry full provenance (source_region_ids,
  processing_config_ids, quality_issues, execution_status, crop_assets,
  source_spans); element-less (failed/unsupported) outcomes stay
  self-sufficient for full source traceback.
* ``figure_ledger`` (t05-1) marks the closed reconciliation; the validator
  enforces detected<->outcome 1:1, completed outcome<->element 1:1 and
  provenance equality on both sides.
* Execution (completed = "real crop asset faithfully assembled") is disjoint
  from quality (needs_review until a review credential exists).

Run::

    .venv/Scripts/python.exe -m pytest tests/test_t05_figure_canonical_assembly.py -q
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zlib
from collections import Counter
from typing import Any
import unittest

from src.application.canonical_assembly import assemble_document
from src.application.canonical_validation import compute_ids, validate_document
from src.domain.canonical_document import SchemaError

_DUMMY_SHA = "b" * 64
_BBOX = [100.0, 200.0, 400.0, 360.0]
_CONFIG_ID = "unified-local-v5:200dpi:no-generated-description:cfg"
_DEFAULT_BBOX = object()  # sentinel: use the standard fixture bbox


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _png_bytes(width: int = 3, height: int = 2) -> bytes:
    """A minimal REAL PNG (8-bit RGB) with an honest IHDR."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x40\x80\xc0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


_PNG = _png_bytes(3, 2)
_ASSET_SHA = hashlib.sha256(_PNG).hexdigest()
_ASSET_REF = "assets/fig-0001.png"  # relative to the test cwd (tmp dir)


class _AssetDir:
    """A temp cwd containing the real asset file at ``_ASSET_REF``."""

    def __init__(self, ref: str = _ASSET_REF, data: bytes = _PNG):
        self.tmp = tempfile.mkdtemp(prefix="t05-assets-")
        path = os.path.join(self.tmp, ref)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        self._prev_cwd: str | None = None

    def __enter__(self) -> "_AssetDir":
        self._prev_cwd = os.getcwd()
        os.chdir(self.tmp)
        return self

    def __exit__(self, *exc) -> None:
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)


def _pi_image_region(
    region_id: str = "up-image00000000000001",
    bbox: Any = _DEFAULT_BBOX,
    *,
    caption: Any = None,
    figure_number: str | None = None,
    relations: list[dict] | None = None,
    issues: list[str] | None = None,
    exec_status: str = "completed",
    image_ref: str | None = _ASSET_REF,
    image_sha256: str | None = _ASSET_SHA,
    context: list[dict] | None = None,
    config_id: str = _CONFIG_ID,
) -> dict:
    region = {
        "region_id": region_id, "element_id": region_id, "kind": "image",
        "bbox": list(_BBOX) if bbox is _DEFAULT_BBOX else bbox,
        "execution_status": exec_status, "quality_status": "needs_review",
        "issues": list(issues or []), "relations": list(relations or []),
        "physical_page": 1,
        "processing_config_id": config_id,
    }
    if caption is not None:
        region["caption"] = caption
    if figure_number is not None:
        region["figure_number"] = figure_number
    if image_ref is not None:
        region["image_ref"] = image_ref
    if image_sha256 is not None:
        region["image_sha256"] = image_sha256
    if context is not None:
        region["context"] = context
    return region


def _caption_block(text: str, page: int = 1) -> dict:
    return {"text": text, "bbox": [120.0, 365.0, 300.0, 380.0],
            "physical_page": page, "element_id": "p0001-b0019"}


def _caption_relation(text: str, page: int = 1) -> dict:
    return {
        "kind": "caption",
        "source": {"text": text, "bbox": [120.0, 365.0, 300.0, 380.0],
                   "physical_page": page, "element_id": "p0001-b0019"},
        "verified": False,
        "basis": "literal_figure_identifier_unique_nearby_caption",
    }


def _body_reference_relation(text: str, page: int = 1) -> dict:
    return {
        "kind": "explicit_body_figure_reference",
        "source": {"physical_page": page, "element_id": "p0001-b0002",
                   "bbox": [60.0, 100.0, 300.0, 120.0], "text": text},
        "basis": "unique_literal_figure_number",
        "verified": False,
    }


def _page(rows: list[dict], pno: int = 1,
          elements: list[dict] | None = None) -> dict:
    return {
        "physical_page": pno, "width": 595.0, "height": 842.0, "rotation": 0,
        "extraction_route": "full_ocr", "decision_status": "approved",
        "decision_reasons": [], "parser_name": "paddleocr-text",
        "elements": elements if elements is not None else [
            {"element_id": f"p{pno}-t1",
             "text": "式中 L 为长度，取值见表 3。",
             "bbox": [60.0, 300.0, 500.0, 320.0]}],
        "regions": rows, "tables": [],
    }


def _page_doc(pages: list[dict], *, sha: str = _DUMMY_SHA) -> dict:
    return {
        "sha256": sha, "file_name": "synthetic-t05.pdf",
        "config_hash": "c" * 64, "processing_run_id": "run_test",
        "metadata": {"document_kind": "standard"}, "pages": pages,
    }


def _assemble(page_doc: dict) -> dict:
    doc = assemble_document(page_doc)
    ids = compute_ids(doc)
    doc.update(ids)
    validate_document(doc)
    return doc


def _figure_outcomes(doc: dict) -> list[dict]:
    return [o for o in doc.get("structured_outcomes") or []
            if o.get("kind") == "figure"]


def _figure_elements(doc: dict) -> list[dict]:
    return [e for e in doc.get("elements") or [] if e.get("type") == "figure"]


# --------------------------------------------------------------------------- #
# 1. Outcome closure: one closed outcome per detected image region
# --------------------------------------------------------------------------- #

class TestOutcomeClosure(unittest.TestCase):
    def test_happy_path_one_completed_outcome_and_element(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统示意图"),
                figure_number="图6")])]))
        outcomes = _figure_outcomes(doc)
        self.assertEqual(len(outcomes), 1)
        o = outcomes[0]
        self.assertEqual(o["execution_status"], "completed")
        self.assertEqual(o["quality_status"], "needs_review")
        self.assertEqual(o["physical_page"], 1)
        self.assertEqual(o["selection_basis"], "single_admissible")
        elements = _figure_elements(doc)
        self.assertEqual(len(elements), 1)
        self.assertEqual(o["element_id"], elements[0]["element_id"])
        ledger = doc.get("figure_ledger")
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["version"], "t05-1")
        self.assertEqual(ledger["outcome_count"], 1)

    def test_invalid_bbox_closes_unsupported_never_silent(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                bbox=None)])]))
        outcomes = _figure_outcomes(doc)
        self.assertEqual(len(outcomes), 1)
        o = outcomes[0]
        self.assertEqual(o["execution_status"], "unsupported")
        self.assertIn("invalid_detection_coordinates", o["issues"])
        self.assertIsNone(o["bbox"])
        self.assertEqual(_figure_elements(doc), [])
        self.assertEqual(doc["figure_ledger"]["outcome_count"], 1)

    def test_inverted_bbox_closes_unsupported(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                bbox=[200.0, 100.0, 150.0, 360.0])])]))  # x0 > x1
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "unsupported")
        self.assertIn("invalid_detection_coordinates", o["issues"])

    def test_missing_asset_closes_failed(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_ref=None, image_sha256=None)])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIn("missing_crop_asset", o["issues"])
        self.assertEqual(o["crop_assets"], [])
        self.assertEqual(_figure_elements(doc), [])

    def test_missing_asset_file_closes_failed(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_ref="assets/does-not-exist.png")])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIn("crop_asset_file_missing", o["issues"])
        self.assertEqual(_figure_elements(doc), [])

    def test_sha_mismatch_closes_failed(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_sha256="e" * 64)])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIn("crop_asset_sha_mismatch", o["issues"])
        self.assertEqual(_figure_elements(doc), [])

    def test_unreadable_image_closes_failed(self):
        garbage = b"definitely not a png" * 8
        with _AssetDir(data=garbage):
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_sha256=hashlib.sha256(garbage).hexdigest())])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIn("crop_asset_unreadable", o["issues"])
        self.assertEqual(_figure_elements(doc), [])

    def test_detection_failure_recorded_verbatim(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                exec_status="failed",
                issues=["detector_stage_error:img-7"])])]))
        o = _figure_outcomes(doc)[0]
        self.assertIn("detection_execution_status:failed", o["issues"])
        self.assertIn("detector_stage_error:img-7", o["issues"])

    def test_failed_outcome_is_self_sufficient_for_traceback(self):
        # element-less path still carries region identity, bbox, configs, the
        # recorded crop provenance and the structured relations.
        rel = _body_reference_relation("如图6所示。")
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_sha256="e" * 64,  # sha mismatch -> failed
                relations=[_caption_relation("图6 排水系统示意图"), rel])])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIsNone(o["element_id"])
        self.assertEqual(o["region_id"], "up-image00000000000001")
        self.assertEqual(o["bbox"], list(_BBOX))
        self.assertEqual(o["detection_config_id"], _CONFIG_ID)
        self.assertEqual(o["recognition_config_id"], _CONFIG_ID)
        # the audit trail records what UPSTREAM recorded (the mismatching
        # sha is kept verbatim -- it is the upstream truth, not our verdict)
        self.assertEqual(o["crop_assets"],
                         [{"ref": _ASSET_REF, "sha256": "e" * 64}])
        self.assertEqual(len(o["cross_page_relations"]), 2)
        kinds = {r["kind"] for r in o["cross_page_relations"]}
        self.assertEqual(kinds, {"caption", "explicit_body_figure_reference"})

    def test_zero_figures_doc_still_writes_empty_ledger(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([])]))
        self.assertEqual(doc["figure_ledger"],
                         {"version": "t05-1", "outcome_count": 0})
        self.assertEqual(_figure_outcomes(doc), [])


# --------------------------------------------------------------------------- #
# 2. Real asset fidelity: no fabricated refs, digests or pixel sizes
# --------------------------------------------------------------------------- #

class TestRealAsset(unittest.TestCase):
    def test_element_asset_is_the_real_pi_asset(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region()])]))
            file_digest = hashlib.sha256(open(_ASSET_REF, "rb").read()).hexdigest()
        el = _figure_elements(doc)[0]
        asset = el["content"]["asset"]
        self.assertIsNotNone(asset)
        self.assertEqual(asset["ref"], _ASSET_REF)
        self.assertNotIn("placeholder", asset["ref"])
        self.assertEqual(asset["sha256"], _ASSET_SHA)
        self.assertEqual(asset["sha256"], file_digest)
        self.assertEqual(asset["media_type"], "image/png")
        # TRUE pixel size read from the IHDR, not pt * 96/72 guessing
        self.assertEqual(asset["pixel_width"], 3)
        self.assertEqual(asset["pixel_height"], 2)

    def test_crop_assets_audit_matches_on_element_and_outcome(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region()])]))
        el = _figure_elements(doc)[0]
        o = _figure_outcomes(doc)[0]
        expected = [{"ref": _ASSET_REF, "sha256": _ASSET_SHA}]
        self.assertEqual(el["crop_assets"], expected)
        self.assertEqual(o["crop_assets"], expected)

    def test_attempt_binds_real_asset_identity(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region()])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(len(o["attempts"]), 1)
        attempt = o["attempts"][0]
        self.assertEqual(attempt["route"], "image_crop")
        self.assertEqual(attempt["execution_status"], "completed")
        self.assertTrue(attempt["selected"])
        self.assertEqual(attempt["input_identity"],
                         f"asset-sha256:{_ASSET_SHA}")
        self.assertEqual(attempt["output_identity"],
                         f"asset-sha256:{_ASSET_SHA}")
        self.assertEqual(attempt["config_id"], _CONFIG_ID)
        self.assertIsNone(attempt["confidence"])  # no upstream score: never invented
        self.assertEqual(o["selected_result_id"], attempt["result_id"])

    def test_failed_attempt_keeps_recorded_asset_but_no_output(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                image_ref="assets/gone.png")])]))
        o = _figure_outcomes(doc)[0]
        attempt = o["attempts"][0]
        self.assertEqual(attempt["execution_status"], "failed")
        self.assertEqual(attempt["failure_reason"], "crop_asset_file_missing")
        self.assertFalse(attempt["selected"])
        # output identity never claims the asset when the crop did not verify
        self.assertNotEqual(attempt["output_identity"],
                            f"asset-sha256:{_ASSET_SHA}")
        self.assertEqual(attempt["output_identity"], attempt["result_id"])


# --------------------------------------------------------------------------- #
# 3. Caption / figure_number: PI structured fields only, never regex guesses
# --------------------------------------------------------------------------- #

class TestCaptionBinding(unittest.TestCase):
    def test_caption_and_figure_number_verbatim(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统示意图"),
                figure_number="图6")])]))
        el = _figure_elements(doc)[0]
        self.assertEqual(el["content"]["caption"], "图6 排水系统示意图")
        self.assertEqual(el["label"], "图6")

    def test_caption_not_found_stays_missing(self):
        # upstream explicitly recorded caption_not_found: the caption stays
        # None -- a nearby "图N ..." text is NOT promoted to a caption.
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region(issues=["caption_not_found"])],
                elements=[{"element_id": "p1-t1",
                           "text": "图9 某个看起来像题图的文本",
                           "bbox": [120.0, 365.0, 300.0, 380.0]}])]))
        el = _figure_elements(doc)[0]
        self.assertIsNone(el["content"]["caption"])
        self.assertIn("caption_not_found", el["quality_issues"])
        # the nearby text survives as its own text element (no silent consume)
        texts = [e["content"]["text"] for e in doc["elements"]
                 if e["type"] == "text"]
        self.assertTrue(any("图9" in t for t in texts), texts)

    def test_caption_text_slot_consumed_when_it_matches_pi_caption(self):
        cap_text = "图3 圆形管渠横断面示意图"
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region(caption=_caption_block(cap_text))],
                elements=[{"element_id": "p1-t1", "text": cap_text,
                           "bbox": [120.0, 365.0, 300.0, 380.0]}])]))
        fig = _figure_elements(doc)[0]
        self.assertEqual(fig["content"]["caption"], cap_text)
        texts = [e["content"]["text"] for e in doc["elements"]
                 if e["type"] == "text"]
        self.assertFalse(any(cap_text == t for t in texts),
                         "caption text must be consumed exactly once")

    def test_no_regex_label_derivation_without_figure_number(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图7 沉淀池构造图"))])]))
        el = _figure_elements(doc)[0]
        self.assertEqual(el["content"]["caption"], "图7 沉淀池构造图")
        self.assertIsNone(el["label"])  # PI has no figure_number: no guessing


# --------------------------------------------------------------------------- #
# 3b. T05-R2 REJECT: figure text consumption is explicit-identity-only
# --------------------------------------------------------------------------- #

class TestFigureTextConsumption(unittest.TestCase):
    """Figure text consumption must not be heuristic (REJECT round 2):

    * geometric containment NEVER consumes a figure's text (the table/formula
      containment contracts are unchanged);
    * the "表N" rule binds to tables only -- a figure is never a host;
    * a figure consumes its caption text ONLY when the PI caption/relation
      carries an explicit identity (element_id or same-page bbox) that uniquely
      binds the text slot; text equality alone or non-unique candidates never
      consume (no first-wins), and the unresolved/ambiguous state is recorded
      on the figure outcomes.
    """

    @staticmethod
    def _texts(doc: dict) -> list[str]:
        return [e["content"]["text"] for e in doc["elements"]
                if e["type"] == "text"]

    def test_plain_text_inside_figure_bbox_stays_independent(self):
        # text geometrically contained in the figure bbox is NOT consumed
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region()],
                elements=[{"element_id": "p1-t1",
                           "text": "设备参数说明文字，位于图内。",
                           "bbox": [150.0, 250.0, 350.0, 300.0]}])]))
        self.assertEqual(_figure_elements(doc)[0]["consumed_sources"], [])
        self.assertTrue(any("设备参数" in t for t in self._texts(doc)),
                        "text inside the figure bbox must survive verbatim")

    def test_table_label_on_figure_only_page_not_consumed(self):
        # "表N" binds to tables only: a figure must never become its host
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region()],
                elements=[{"element_id": "p1-t1",
                           "text": "表1 主要设备一览表",
                           "bbox": [150.0, 250.0, 350.0, 300.0]}])]))
        self.assertEqual(_figure_elements(doc)[0]["consumed_sources"], [])
        self.assertTrue(any("表1" in t for t in self._texts(doc)),
                        "'表1' text must survive on a figure-only page")

    def test_text_equality_alone_never_consumes(self):
        # the caption anchor (element_id + bbox) points at a DIFFERENT element
        # than the equal-text slot: no explicit binding -> no consumption and
        # an unresolved issue on the outcome
        cap_text = "图4 布置图"
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region(caption=_caption_block(cap_text))],
                elements=[{"element_id": "p1-other", "text": cap_text,
                           "bbox": [400.0, 600.0, 500.0, 620.0]}])]))
        fig = _figure_elements(doc)[0]
        self.assertEqual(fig["consumed_sources"], [])
        self.assertTrue(any(cap_text == t for t in self._texts(doc)))
        o = _figure_outcomes(doc)[0]
        self.assertTrue(
            any(i.startswith("figure_caption_binding_unresolved")
                for i in o["issues"]), o["issues"])

    def test_caption_element_id_unique_binding_consumes(self):
        cap_text = "图5 流程示意图"
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region(caption={
                    "text": cap_text, "bbox": [120.0, 365.0, 300.0, 380.0],
                    "physical_page": 1, "element_id": "p1-cap"})],
                elements=[{"element_id": "p1-cap", "text": cap_text,
                           "bbox": [120.0, 365.0, 300.0, 380.0]}])]))
        cs = _figure_elements(doc)[0]["consumed_sources"]
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["element_id"], "p1-cap")
        self.assertEqual(cs[0]["physical_page"], 1)
        self.assertEqual(cs[0]["basis"], "element_id")
        self.assertFalse(any(cap_text == t for t in self._texts(doc)))

    def test_caption_bbox_unique_binding_consumes(self):
        # bbox-only anchor (no element_id in the caption block) still binds
        cap_text = "图8 断面图"
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region(caption={
                    "text": cap_text, "bbox": [200.0, 240.0, 320.0, 270.0],
                    "physical_page": 1})],
                elements=[{"element_id": "p1-t1", "text": cap_text,
                           "bbox": [200.0, 240.0, 320.0, 270.0]}])]))
        cs = _figure_elements(doc)[0]["consumed_sources"]
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs[0]["element_id"], "p1-t1")
        self.assertEqual(cs[0]["basis"], "bbox")
        self.assertFalse(any(cap_text == t for t in self._texts(doc)))

    def test_same_caption_two_figures_never_first_wins(self):
        # identical caption text, no explicit anchors on either figure: the
        # text is NOT consumed and BOTH outcomes record the ambiguity
        cap_text = "图9 系统示意图"
        no_anchor = {"text": cap_text, "physical_page": 1}
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region("up-i1", bbox=[100.0, 200.0, 400.0, 260.0],
                                  caption=dict(no_anchor)),
                 _pi_image_region("up-i2", bbox=[100.0, 400.0, 400.0, 460.0],
                                  caption=dict(no_anchor))],
                elements=[{"element_id": "p1-t1", "text": cap_text,
                           "bbox": [120.0, 365.0, 300.0, 380.0]}])]))
        for fig in _figure_elements(doc):
            self.assertEqual(fig["consumed_sources"], [])
        self.assertTrue(any(cap_text == t for t in self._texts(doc)))
        for o in _figure_outcomes(doc):
            self.assertTrue(
                any(i.startswith("figure_caption_binding_ambiguous")
                    for i in o["issues"]), o["issues"])

    def test_same_caption_disambiguated_by_unique_anchors(self):
        # identical caption text but each figure's anchor points at its own
        # text slot: each figure consumes exactly its own caption element
        cap_text = "图2 系统图"
        r1 = _pi_image_region("up-i1", bbox=[100.0, 200.0, 400.0, 260.0],
                              caption={"text": cap_text,
                                       "bbox": [120.0, 365.0, 300.0, 380.0],
                                       "physical_page": 1,
                                       "element_id": "p1-cap-a"})
        r2 = _pi_image_region("up-i2", bbox=[100.0, 400.0, 400.0, 460.0],
                              caption={"text": cap_text,
                                       "bbox": [120.0, 470.0, 300.0, 485.0],
                                       "physical_page": 1,
                                       "element_id": "p1-cap-b"})
        t_a = {"element_id": "p1-cap-a", "text": cap_text,
               "bbox": [120.0, 365.0, 300.0, 380.0]}
        t_b = {"element_id": "p1-cap-b", "text": cap_text,
               "bbox": [120.0, 470.0, 300.0, 485.0]}
        with _AssetDir():
            a = _assemble(_page_doc([_page([r1, r2], elements=[t_a, t_b])]))
            b = _assemble(_page_doc([_page(
                [copy.deepcopy(r2), copy.deepcopy(r1)],
                elements=[copy.deepcopy(t_b), copy.deepcopy(t_a)])]))
        for doc in (a, b):
            by_region = {}
            for fig in _figure_elements(doc):
                self.assertEqual(len(fig["consumed_sources"]), 1)
                by_region[fig["source_region_ids"][0]] = fig
            self.assertEqual(
                by_region["up-i1"]["consumed_sources"][0]["element_id"],
                "p1-cap-a")
            self.assertEqual(
                by_region["up-i2"]["consumed_sources"][0]["element_id"],
                "p1-cap-b")
            self.assertFalse(any(cap_text == t for t in self._texts(doc)))
        self.assertEqual(a["canonical_id"], b["canonical_id"])
        self.assertEqual(a["canonical_content_id"], b["canonical_content_id"])

    def test_ambiguous_caption_reverse_order_stable_ids(self):
        # reversing the input order must not flip the (non-)consumption,
        # the recorded issues or any id
        cap_text = "图9 系统示意图"
        no_anchor = {"text": cap_text, "physical_page": 1}
        t = {"element_id": "p1-t1", "text": cap_text,
             "bbox": [120.0, 365.0, 300.0, 380.0]}
        r1 = _pi_image_region("up-i1", bbox=[100.0, 200.0, 400.0, 260.0],
                              caption=dict(no_anchor))
        r2 = _pi_image_region("up-i2", bbox=[100.0, 400.0, 400.0, 460.0],
                              caption=dict(no_anchor))
        with _AssetDir():
            a = _assemble(_page_doc([_page([r1, r2], elements=[t])]))
            b = _assemble(_page_doc([_page(
                [copy.deepcopy(r2), copy.deepcopy(r1)],
                elements=[copy.deepcopy(t)])]))
        for doc in (a, b):
            for fig in _figure_elements(doc):
                self.assertEqual(fig["consumed_sources"], [])
            self.assertTrue(any(cap_text == t2 for t2 in self._texts(doc)))
        self.assertEqual(a["canonical_id"], b["canonical_id"])
        self.assertEqual(a["canonical_content_id"], b["canonical_content_id"])


# --------------------------------------------------------------------------- #
# 4. References / relations: PI explicit relations only
# --------------------------------------------------------------------------- #

class TestReferencesAndRelations(unittest.TestCase):
    def test_explicit_reference_becomes_reference_and_record(self):
        rel = _body_reference_relation("排水管道布置如图6所示。")
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                relations=[rel])])]))
        el = _figure_elements(doc)[0]
        o = _figure_outcomes(doc)[0]
        refs = el["content"]["references"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["text"], "排水管道布置如图6所示。")
        self.assertEqual(len(refs[0]["source_span_ids"]), 1)
        records = [r for r in el["cross_page_relations"]
                   if r["kind"] == "explicit_body_figure_reference"]
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["basis"], "unique_literal_figure_number")
        self.assertIs(rec["verified"], False)
        self.assertEqual(rec["source"]["physical_page"], 1)
        self.assertEqual(rec["source"]["bbox"], [60.0, 100.0, 300.0, 120.0])
        self.assertEqual(rec["source"]["text"], "排水管道布置如图6所示。")
        self.assertEqual(rec["target"], None)
        self.assertTrue(rec["relation_id"].startswith("irel-"))
        # the outcome mirrors the element records (element projection wins)
        self.assertEqual(o["cross_page_relations"], el["cross_page_relations"])

    def test_body_scan_never_invents_references(self):
        # body text mentioning （图7） but NO upstream relation: no reference
        with _AssetDir():
            doc = _assemble(_page_doc([_page(
                [_pi_image_region()],
                elements=[{"element_id": "p1-t1",
                           "text": "管道布置见图7（此处正文中提到图）。",
                           "bbox": [60.0, 100.0, 500.0, 120.0]}])]))
        el = _figure_elements(doc)[0]
        self.assertEqual(el["content"]["references"], [])
        self.assertEqual(el["cross_page_relations"], [])

    def test_caption_relation_record_preserves_binding(self):
        cap = "图6 排水系统示意图"
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block(cap),
                relations=[_caption_relation(cap)])])]))
        el = _figure_elements(doc)[0]
        rec = next(r for r in el["cross_page_relations"]
                   if r["kind"] == "caption")
        self.assertEqual(rec["basis"],
                         "literal_figure_identifier_unique_nearby_caption")
        self.assertIs(rec["verified"], False)
        self.assertEqual(rec["source"]["text"], cap)
        self.assertEqual(rec["source"]["physical_page"], 1)

    def test_reference_span_projects_only_on_parsed_pages(self):
        # parsed source page: the context span projects and the record binds it
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                relations=[_body_reference_relation("如图6所示。")])])]))
        el = _figure_elements(doc)[0]
        rec = next(r for r in el["cross_page_relations"]
                   if r["kind"] == "explicit_body_figure_reference")
        self.assertIsNotNone(rec["source_span_id"])
        span = next(s for s in el["source_spans"]
                    if s["span_id"] == rec["source_span_id"])
        self.assertEqual(span["role"], "context")
        self.assertEqual(span["physical_page"], 1)

        # non-parsed source page: the record keeps the text but no span is
        # projected (a span on a blank/failed page would violate invariant 9)
        blank = _page([], pno=2)
        blank["extraction_route"] = "failed"
        blank["decision_status"] = "failed"
        with _AssetDir():
            doc2 = _assemble(_page_doc([
                _page([_pi_image_region(
                    relations=[_body_reference_relation("如图6所示。", page=2)])]),
                blank,
            ]))
        el2 = _figure_elements(doc2)[0]
        rec2 = next(r for r in el2["cross_page_relations"]
                    if r["kind"] == "explicit_body_figure_reference")
        self.assertIsNone(rec2["source_span_id"])
        self.assertEqual(rec2["source"]["text"], "如图6所示。")
        self.assertEqual(rec2["source"]["physical_page"], 2)



# --------------------------------------------------------------------------- #
# 5. Element provenance + execution/quality separation
# --------------------------------------------------------------------------- #

class TestElementProvenance(unittest.TestCase):
    def test_element_carries_full_provenance(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                issues=["caption_and_reference_require_review"])])]))
        el = _figure_elements(doc)[0]
        o = _figure_outcomes(doc)[0]
        self.assertEqual(el["source_region_ids"], ["up-image00000000000001"])
        self.assertEqual(el["processing_config_ids"], [_CONFIG_ID])
        self.assertIn("caption_and_reference_require_review",
                      el["quality_issues"])
        self.assertEqual(el["execution_status"], "completed")
        self.assertEqual(el["quality_status"], "needs_review")
        self.assertIn("structured_default_locator_only", el["policy_reasons"])
        # primary span + real provenance operation
        roles = [s["role"] for s in el["source_spans"]]
        self.assertIn("primary", roles)
        methods = [p["method"] for p in el["provenance"]]
        self.assertIn("image_extraction", methods)
        self.assertEqual(o["region_id"] in el["source_region_ids"], True)

    def test_completed_never_implies_quality_passed(self):
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region()])]))
        el = _figure_elements(doc)[0]
        o = _figure_outcomes(doc)[0]
        # execution completed, but content quality stays needs_review and no
        # fabricated OCR/description text is attached
        self.assertEqual(o["execution_status"], "completed")
        self.assertEqual(el["quality_status"], "needs_review")
        self.assertEqual(o["quality_status"], "needs_review")
        self.assertNotIn("latex", el["content"])
        self.assertNotIn("text", el["content"])


# --------------------------------------------------------------------------- #
# 6. figure_ledger closure + validator enforcement
# --------------------------------------------------------------------------- #

class TestLedgerAndValidator(unittest.TestCase):
    def _good_doc(self) -> dict:
        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统示意图"),
                figure_number="图6",
                relations=[_body_reference_relation("如图6所示。")])])]))
        return doc

    def test_marker_count_mismatch_rejected(self):
        doc = self._good_doc()
        doc["figure_ledger"]["outcome_count"] = 5
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_figure_elements_without_outcomes_rejected_on_marked_doc(self):
        doc = self._good_doc()
        doc["structured_outcomes"] = [
            o for o in doc["structured_outcomes"] if o.get("kind") != "figure"]
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_removing_marker_keeps_self_consistent_doc_valid(self):
        doc = self._good_doc()
        doc.pop("figure_ledger")
        doc.update(compute_ids(doc))  # recompute: the marker was audit input
        validate_document(doc)  # outcomes still reconcile the elements

    def test_dropping_marker_does_not_hide_divergence(self):
        doc = self._good_doc()
        doc["structured_outcomes"] = [
            o for o in doc["structured_outcomes"] if o.get("kind") != "figure"]
        doc["figure_ledger"]["outcome_count"] = 0
        # marker stays declared with 0 outcomes, yet figure elements exist:
        # the reconciliation bypass must be rejected
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_tampered_crop_assets_rejected(self):
        doc = self._good_doc()
        el = _figure_elements(doc)[0]
        el["crop_assets"][0]["sha256"] = "f" * 64
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_broken_source_trace_rejected(self):
        doc = self._good_doc()
        el = _figure_elements(doc)[0]
        el["source_region_ids"] = ["up-somewhere-else"]
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_config_divergence_rejected(self):
        doc = self._good_doc()
        o = _figure_outcomes(doc)[0]
        o["recognition_config_id"] = "unified-local-v5:other"
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_selected_attempt_config_mismatch_rejected(self):
        doc = self._good_doc()
        el = _figure_elements(doc)[0]
        o = _figure_outcomes(doc)[0]
        # the outcome declares a recognition config the selected attempt never
        # used -- the selected attempt must match recognition_config_id
        el["processing_config_ids"] = [_CONFIG_ID, "cfg:recognition-only"]
        o["recognition_config_id"] = "cfg:recognition-only"
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_relation_tamper_rejected(self):
        doc = self._good_doc()
        el = _figure_elements(doc)[0]
        el["cross_page_relations"][0]["basis"] = "fabricated-basis"
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_passed_quality_requires_review_assertion(self):
        doc = self._good_doc()
        o = _figure_outcomes(doc)[0]
        o["quality_status"] = "passed"
        with self.assertRaises(SchemaError):
            validate_document(doc)  # the semantic gate fires before digests
        o["issues"] = ["review_assertion:reviewer-7"]
        el = _figure_elements(doc)[0]
        el["quality_status"] = "passed"
        doc.update(compute_ids(doc))
        validate_document(doc)  # proper credential: accepted

    def test_route_vocabulary_is_kind_scoped(self):
        doc = self._good_doc()
        o = _figure_outcomes(doc)[0]
        o["attempts"][0]["route"] = "cropped_table"  # a TABLE route
        with self.assertRaises(SchemaError):
            validate_document(doc)

    def test_duplicate_figure_source_region_id_hard_fails(self):
        with _AssetDir():
            page = _page([_pi_image_region("up-dup"),
                          _pi_image_region("up-dup",
                                           bbox=[100.0, 400.0, 400.0, 500.0])])
            with self.assertRaises(ValueError) as ctx:
                assemble_document(_page_doc([page]))
        self.assertIn("figure source region_id", str(ctx.exception))


# --------------------------------------------------------------------------- #
# 7. Determinism + identity flips
# --------------------------------------------------------------------------- #

class TestDeterminismAndIdentity(unittest.TestCase):
    def test_input_order_does_not_change_ids(self):
        def build(regions):
            with _AssetDir():
                doc = _assemble(_page_doc([_page(regions)]))
            return doc

        r1 = _pi_image_region("up-i1", bbox=[100.0, 200.0, 400.0, 260.0],
                              caption=_caption_block("图1", 1))
        r2 = _pi_image_region("up-i2", bbox=[100.0, 400.0, 400.0, 460.0],
                              caption=_caption_block("图2", 1))
        a = build([r1, r2])
        b = build([copy.deepcopy(r2), copy.deepcopy(r1)])
        self.assertEqual(a["canonical_id"], b["canonical_id"])
        self.assertEqual(a["canonical_content_id"], b["canonical_content_id"])
        self.assertEqual([e["element_id"] for e in _figure_elements(a)],
                         [e["element_id"] for e in _figure_elements(b)])
        self.assertEqual(
            sorted(o["region_id"] for o in _figure_outcomes(a)),
            sorted(o["region_id"] for o in _figure_outcomes(b)))

    def test_content_change_flips_both_ids(self):
        with _AssetDir():
            base = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统示意图"))])]))
            changed = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统剖面示意图"))])]))
        self.assertNotEqual(base["canonical_content_id"],
                            changed["canonical_content_id"])
        self.assertNotEqual(base["canonical_id"], changed["canonical_id"])

    def test_audit_change_flips_canonical_only(self):
        with _AssetDir():
            base = _assemble(_page_doc([_page([_pi_image_region()])]))
            changed = _assemble(_page_doc([_page([_pi_image_region(
                issues=["extra_downstream_issue"])])]))
        self.assertEqual(base["canonical_content_id"],
                         changed["canonical_content_id"])
        self.assertNotEqual(base["canonical_id"], changed["canonical_id"])

    def test_config_change_flips_canonical_only(self):
        with _AssetDir():
            base = _assemble(_page_doc([_page([_pi_image_region()])]))
            changed = _assemble(_page_doc([_page([_pi_image_region(
                config_id="unified-local-v5:other:cfg")])]))
        self.assertEqual(base["canonical_content_id"],
                         changed["canonical_content_id"])
        self.assertNotEqual(base["canonical_id"], changed["canonical_id"])


# --------------------------------------------------------------------------- #
# 8. Chunk policy: locator-only, quality/execution separation survives
# --------------------------------------------------------------------------- #

class _CharTokenizer:
    def count(self, text: str) -> str:
        return len(text)

    def encode(self, text: str, **_kwargs):  # noqa: D401
        return list(text)


class TestChunkPolicy(unittest.TestCase):
    def test_figure_chunk_is_locator_only(self):
        from src.application.v2_chunker import chunk_document

        with _AssetDir():
            doc = _assemble(_page_doc([_page([_pi_image_region(
                caption=_caption_block("图6 排水系统示意图"))])]))
        chunks = chunk_document(doc, token_budget=480, overlap=0,
                                tokenizer=_CharTokenizer())
        fig_chunks = [c for c in chunks
                      if "figure" in c.metadata.get("element_types", [])]
        self.assertTrue(fig_chunks, "expected a figure chunk")
        for c in fig_chunks:
            self.assertEqual(c.metadata["usage_policy"], "source_locator_only")
            self.assertEqual(c.metadata["quality_status"], "needs_review")


# --------------------------------------------------------------------------- #
# 9. Legacy compatibility + real-corpus read-only reconciliation
# --------------------------------------------------------------------------- #

class TestLegacyCompat(unittest.TestCase):
    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_published_artifact_still_validates_unmarked(self):
        import glob
        published = sorted(glob.glob(os.path.join(
            self._REPO, "data", "canonical", "v2", "*",
            "*.canonical.json")))
        if not published:
            self.skipTest("no published canonical artifacts present")
        sample = None
        for path in published:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            if any(e.get("type") == "figure" for e in doc.get("elements") or []):
                sample = doc
                break
        if sample is None:
            self.skipTest("no published artifact with figure elements")
        self.assertNotIn("figure_ledger", sample)  # unmarked legacy contract
        validate_document(sample)  # must keep validating

    def test_legacy_pi_without_image_fields_still_closes(self):
        # a historical page-intermediate may record no crop asset at all --
        # the region closes as an explicit failed outcome, never silently.
        region = _pi_image_region(image_ref=None, image_sha256=None)
        region.pop("processing_config_id", None)
        with _AssetDir():
            doc = assemble_document(_page_doc([_page([region])]))
        o = _figure_outcomes(doc)[0]
        self.assertEqual(o["execution_status"], "failed")
        self.assertIn("missing_crop_asset", o["issues"])
        self.assertIsNone(o["detection_config_id"])


class TestRealCorpusReconciliation(unittest.TestCase):
    """Read-only: assemble every corpus document in memory and reconcile the
    86 detected image regions region by region. Writes nothing."""

    _REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # The current published corpus PI (23 docs, 86 image regions across 13
    # docs) -- the T05 reconciliation target.
    _PI = os.path.join(_REPO, "data", "canonical",
                       "corpus-069284e6d60e-documents.jsonl")

    def test_corpus_reconciles(self):
        if not os.path.isfile(self._PI):
            self.skipTest("real corpus page-intermediate not present")
        totals: Counter = Counter()
        failures: list[str] = []
        prev_cwd = os.getcwd()
        os.chdir(self._REPO)  # image_ref paths are repo-root relative
        try:
            with open(self._PI, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    page_doc = json.loads(line)
                    sha = page_doc["sha256"]
                    detected = [
                        (p.get("physical_page"), r)
                        for p in page_doc.get("pages") or []
                        for r in p.get("regions") or []
                        if r.get("kind") == "image"]
                    doc = assemble_document(page_doc)
                    doc.update(compute_ids(doc))
                    outcomes = _figure_outcomes(doc)
                    self.assertEqual(
                        len(outcomes), len(detected),
                        f"{sha[:12]}: one outcome per detected image region")
                    elements = _figure_elements(doc)
                    completed = [o for o in outcomes
                                 if o["execution_status"] == "completed"]
                    self.assertEqual(
                        len(completed), len(elements),
                        f"{sha[:12]}: completed outcomes == figure elements")
                    try:
                        validate_document(doc)
                    except SchemaError as exc:
                        failures.append(f"{sha[:12]}: {exc}")
                    totals["docs"] += 1
                    totals["detected"] += len(detected)
                    totals["outcomes"] += len(outcomes)
                    for o in outcomes:
                        totals[f"exec_{o['execution_status']}"] += 1
                        if o["crop_assets"]:
                            totals["outcome_assets"] += 1
                        totals["outcome_relations"] += len(
                            o["cross_page_relations"])
                        for i in o["issues"]:
                            if i in ("missing_crop_asset",
                                     "crop_asset_file_missing",
                                     "crop_asset_sha_mismatch",
                                     "crop_asset_unreadable",
                                     "invalid_detection_coordinates"):
                                totals[f"issue_{i}"] += 1
                    for e in elements:
                        asset = e["content"].get("asset") or {}
                        if asset and not asset.get("ref", "").startswith(
                                "assets/sha256/placeholder"):
                            totals["real_assets"] += 1
                        if e["content"].get("caption"):
                            totals["captions"] += 1
                        if e.get("label"):
                            totals["labels"] += 1
                        totals["element_relations"] += len(
                            e.get("cross_page_relations") or [])
                    for _p, r in detected:
                        if r.get("caption"):
                            totals["pi_captions"] += 1
                        if r.get("figure_number"):
                            totals["pi_labels"] += 1
                        totals["pi_relations"] += len(r.get("relations") or [])
        finally:
            os.chdir(prev_cwd)
        self.assertEqual(totals["docs"], 23)
        self.assertEqual(totals["detected"], 86)
        self.assertEqual(totals["outcomes"], 86)
        self.assertEqual(totals["exec_completed"], 86)
        self.assertEqual(totals["real_assets"], 86)
        self.assertEqual(totals["captions"], 25)
        self.assertEqual(totals["pi_captions"], 25)
        self.assertEqual(totals["labels"], 25)
        self.assertEqual(totals["element_relations"], totals["pi_relations"])
        self.assertEqual(totals["outcome_relations"], totals["pi_relations"])
        self.assertEqual(failures, [])
        print("\nT05 real-corpus reconciliation:", dict(totals))


if __name__ == "__main__":
    unittest.main()
