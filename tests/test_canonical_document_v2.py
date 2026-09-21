"""Unit tests for the V2 Canonical Document domain model.

Covers (plan 1.2 acceptance):
- discriminated ``content`` union strictness
- unknown-field rejection at every nesting level
- the 17 invariants from design §12
"""

from __future__ import annotations

import copy
import unittest

from src.domain.canonical_document import (
    CanonicalDocument,
    SchemaError,
    SCHEMA_VERSION,
)

SHA = "a" * 64
CONFIG_SHA = "b" * 64
SNAPSHOT_SHA = "c" * 64


def _baseline_doc() -> dict:
    """A minimal valid V2 document (1 parsed page, 1 text element)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_id": f"canonical-sha256:{SHA}",
        "canonical_content_id": f"canonical-content-sha256:{SHA}",
        "metadata_fingerprint": f"metadata-sha256:{SHA}",
        "document_id": f"pdf-sha256:{SHA}",
        "source": {
            "sha256": SHA,
            "file_name": "example.pdf",
            "media_type": "application/pdf",
            "page_count": 1,
            "source_uri": None,
        },
        "metadata": {
            "title": "示例标准",
            "alternate_titles": [],
            "language": "zh-CN",
            "identifiers": [{"scheme": "standard_number", "value": "DB4201/T 651-2021"}],
            "document_kind": "standard",
            "jurisdictions": ["武汉市"],
            "issuing_authorities": [],
            "publication_date": None,
            "effective_from": None,
            "effective_to": None,
            "effective_status": "current",
            "effective_status_as_of": "2026-09-21",
            "registry_snapshot_id": f"source-registry-sha256:{SNAPSHOT_SHA}",
        },
        "generation": {
            "run_id": "run-1",
            "created_at": "2026-09-21T00:00:00Z",
            "pipeline": "v2-pdf-pipeline",
            "pipeline_version": "0.1.0",
            "config_sha256": CONFIG_SHA,
            "engines": [{"engine_id": "native-1", "name": "pymupdf", "version": "1.23"}],
        },
        "pages": [
            {
                "physical_page": 1,
                "printed_label": None,
                "width": 400.0,
                "height": 600.0,
                "unit": "pt",
                "rotation_degrees": 0,
                "parse_status": "parsed",
                "parse_error": None,
            }
        ],
        "elements": [
            {
                "element_id": "e000001",
                "type": "text",
                "role": "clause",
                "label": "2.1",
                "section_path": [{"label": "2", "title": "术语"}],
                "content": {"text": "降雨的全部演变过程。", "list": None},
                "source_spans": [
                    {
                        "span_id": "s000001",
                        "role": "primary",
                        "physical_page": 1,
                        "bbox": [10.0, 10.0, 100.0, 50.0],
                        "orientation_degrees": 0,
                    }
                ],
                "provenance": [
                    {"operation": "extract", "method": "native_text", "engine_id": "native-1", "confidence": 0.9}
                ],
                "links": [],
            }
        ],
    }


def _table_element() -> dict:
    return {
        "element_id": "e000002",
        "type": "table",
        "role": "table",
        "label": "表1",
        "section_path": [],
        "content": {
            "row_count": 2,
            "column_count": 2,
            "caption": "表1 示例",
            "cells": [
                {"row": 0, "column": 0, "row_span": 1, "column_span": 1, "role": "header", "text": "A", "source_span_ids": []},
                {"row": 0, "column": 1, "row_span": 1, "column_span": 1, "role": "header", "text": "B", "source_span_ids": []},
                {"row": 1, "column": 0, "row_span": 1, "column_span": 1, "role": "data", "text": "x", "source_span_ids": []},
                {"row": 1, "column": 1, "row_span": 1, "column_span": 1, "role": "data", "text": "y", "source_span_ids": []},
            ],
            "notes": [],
        },
        "source_spans": [
            {"span_id": "s000002", "role": "primary", "physical_page": 1, "bbox": [10.0, 60.0, 200.0, 200.0], "orientation_degrees": 0}
        ],
        "provenance": [
            {"operation": "recognize", "method": "table_recognition", "engine_id": "native-1", "confidence": 0.8}
        ],
        "links": [],
    }


def _formula_element() -> dict:
    return {
        "element_id": "e000003",
        "type": "formula",
        "role": "display_formula",
        "label": "5.4.2-2",
        "section_path": [{"label": "5.4.2", "title": None}],
        "content": {
            "formula_number": "5.4.2-2",
            "latex": "Q=1",
            "recognized_formula": None,
            "context_text": "式中：Q——流量。",
        },
        "source_spans": [
            {"span_id": "s000003", "role": "primary", "physical_page": 1, "bbox": [10.0, 210.0, 100.0, 230.0], "orientation_degrees": 0},
            {"span_id": "s000004", "role": "context", "physical_page": 1, "bbox": [10.0, 230.0, 200.0, 300.0], "orientation_degrees": 0},
        ],
        "provenance": [
            {"operation": "recognize", "method": "formula_recognition", "engine_id": "native-1", "confidence": 0.7}
        ],
        "links": [],
    }


def _figure_element() -> dict:
    return {
        "element_id": "e000004",
        "type": "figure",
        "role": "figure",
        "label": "图3",
        "section_path": [],
        "content": {
            "caption": "图3 示意图",
            "references": [{"text": "l——AB的弧长（m）（图3）", "source_span_ids": ["s000006"]}],
            "asset": {
                "ref": "assets/sha256/d.png",
                "sha256": "d" * 64,
                "media_type": "image/png",
                "pixel_width": 100,
                "pixel_height": 100,
            },
        },
        "source_spans": [
            {"span_id": "s000005", "role": "primary", "physical_page": 1, "bbox": [10.0, 310.0, 200.0, 400.0], "orientation_degrees": 0},
            {"span_id": "s000006", "role": "context", "physical_page": 1, "bbox": [10.0, 400.0, 200.0, 450.0], "orientation_degrees": 0},
        ],
        "provenance": [
            {"operation": "crop", "method": "image_extraction", "engine_id": "native-1", "confidence": None}
        ],
        "links": [],
    }


class DiscriminatedUnionTest(unittest.TestCase):
    def test_all_four_content_types_accepted(self):
        for elt in (_table_element(), _formula_element(), _figure_element()):
            doc = _baseline_doc()
            doc["elements"].append(elt)
            CanonicalDocument.from_dict(doc)  # no raise

    def test_type_text_with_table_content_shape_rejected(self):
        doc = _baseline_doc()
        doc["elements"][0]["content"] = {"row_count": 1, "column_count": 1, "caption": None, "cells": [], "notes": []}
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_unknown_type_rejected(self):
        doc = _baseline_doc()
        doc["elements"][0]["type"] = "code"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)


class UnknownFieldRejectionTest(unittest.TestCase):
    def _assert_unknown_rejected(self, mutate):
        doc = _baseline_doc()
        mutate(doc)
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_top_level(self):
        self._assert_unknown_rejected(lambda d: d.__setitem__("review_status", "pass"))

    def test_source(self):
        self._assert_unknown_rejected(lambda d: d["source"].__setitem__("extra", 1))

    def test_metadata(self):
        self._assert_unknown_rejected(lambda d: d["metadata"].__setitem__("tags", ["x"]))

    def test_identifier(self):
        self._assert_unknown_rejected(lambda d: d["metadata"]["identifiers"][0].__setitem__("note", "x"))

    def test_generation(self):
        self._assert_unknown_rejected(lambda d: d["generation"].__setitem__("projection_version", "v1"))

    def test_engine(self):
        self._assert_unknown_rejected(lambda d: d["generation"]["engines"][0].__setitem__("x", 1))

    def test_page(self):
        self._assert_unknown_rejected(lambda d: d["pages"][0].__setitem__("quality", 0.5))

    def test_element(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0].__setitem__("truth_level", "gold"))

    def test_section_path_entry(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0]["section_path"][0].__setitem__("depth", 1))

    def test_text_content(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0]["content"].__setitem__("normalized_text", "x"))

    def test_span(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0]["source_spans"][0].__setitem__("confidence", 1.0))

    def test_provenance(self):
        self._assert_unknown_rejected(lambda d: d["elements"][0]["provenance"][0].__setitem__("qa_pass", True))

    def test_link(self):
        doc = _baseline_doc()
        doc["elements"][0]["links"] = [{"type": "references", "target_element_id": "e000001"}]
        doc["elements"][0]["links"][0]["weight"] = 0.5
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_table_cell(self):
        doc = _baseline_doc()
        doc["elements"].append(_table_element())
        doc["elements"][-1]["content"]["cells"][0]["bbox"] = [0, 0, 1, 1]
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)


class InvariantsTest(unittest.TestCase):
    def _assert_invalid(self, mutate):
        doc = _baseline_doc()
        mutate(doc)
        with self.assertRaises((SchemaError, ValueError)):
            CanonicalDocument.from_dict(doc)

    # 1. metadata closed schema -> unknown field rejected
    def test_inv1_metadata_closed(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("tags", ["x"]))

    # 2. effective_status non-unknown requires as_of
    def test_inv2_status_as_of(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("effective_status_as_of", None))

    def test_inv2_unknown_status_allows_null_as_of(self):
        doc = _baseline_doc()
        doc["metadata"]["effective_status"] = "unknown"
        doc["metadata"]["effective_status_as_of"] = None
        CanonicalDocument.from_dict(doc)  # no raise

    # 3. unknown values not guessed -> enforced by literal enums
    def test_inv3_unknown_document_kind_rejected(self):
        self._assert_invalid(lambda d: d["metadata"].__setitem__("document_kind", "law"))

    # 4. pages.length == page_count, continuous and unique
    def test_inv4_page_count_mismatch(self):
        self._assert_invalid(lambda d: d["source"].__setitem__("page_count", 2))

    def test_inv4_non_continuous_pages(self):
        doc = _baseline_doc()
        doc["source"]["page_count"] = 2
        doc["pages"].append(copy.deepcopy(doc["pages"][0]))
        doc["pages"][1]["physical_page"] = 3  # gap
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 5/6. element id uniqueness
    def test_inv5_duplicate_element_id(self):
        doc = _baseline_doc()
        doc["elements"].append(copy.deepcopy(doc["elements"][0]))
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv6_duplicate_span_id(self):
        doc = _baseline_doc()
        doc["elements"].append(_table_element())
        doc["elements"][-1]["source_spans"][0]["span_id"] = "s000001"  # collide with text element
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv6_cell_span_ref_unresolved(self):
        doc = _baseline_doc()
        doc["elements"].append(_table_element())
        doc["elements"][-1]["content"]["cells"][0]["source_span_ids"] = ["nope"]
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv6_link_target_unresolved(self):
        doc = _baseline_doc()
        doc["elements"][0]["links"] = [{"type": "references", "target_element_id": "e999999"}]
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 7. each element has at least one primary span
    def test_inv7_no_primary_span(self):
        doc = _baseline_doc()
        doc["elements"][0]["source_spans"][0]["role"] = "continuation"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 8. bbox within page bounds
    def test_inv8_bbox_out_of_bounds(self):
        doc = _baseline_doc()
        doc["elements"][0]["source_spans"][0]["bbox"] = [0.0, 0.0, 500.0, 50.0]  # x1 > width
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv8_inverted_bbox(self):
        doc = _baseline_doc()
        doc["elements"][0]["source_spans"][0]["bbox"] = [100.0, 10.0, 50.0, 50.0]  # x0 > x1
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 9. blank/failed pages do not contribute spans
    def test_inv9_blank_page_has_span(self):
        doc = _baseline_doc()
        doc["pages"][0]["parse_status"] = "blank"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv9_failed_page_requires_parse_error(self):
        doc = _baseline_doc()
        doc["pages"][0]["parse_status"] = "failed"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv9_blank_page_no_elements_ok(self):
        doc = _baseline_doc()
        doc["pages"][0]["parse_status"] = "blank"
        doc["elements"] = []  # no spans anywhere
        CanonicalDocument.from_dict(doc)  # no raise

    # 10. no public Element.text field -> unknown field rejected
    def test_inv10_no_public_text(self):
        self._assert_invalid(lambda d: d["elements"][0].__setitem__("text", "x"))

    # 11. type matches content schema, unknown content fields rejected
    def test_inv11_text_missing_text_field(self):
        doc = _baseline_doc()
        doc["elements"][0]["content"] = {"list": None}  # missing text
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 12. TextContent.text non-empty
    def test_inv12_empty_text(self):
        doc = _baseline_doc()
        doc["elements"][0]["content"]["text"] = ""
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 13. table grid covers all cells
    def test_inv13_cell_row_out_of_range(self):
        doc = _baseline_doc()
        doc["elements"].append(_table_element())
        doc["elements"][-1]["content"]["cells"][0]["row"] = 5
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv13_row_span_exceeds_grid(self):
        doc = _baseline_doc()
        doc["elements"].append(_table_element())
        doc["elements"][-1]["content"]["cells"][0]["row_span"] = 5
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 14. formula needs latex or recognized_formula
    def test_inv14_formula_without_any_recognition(self):
        doc = _baseline_doc()
        doc["elements"].append(_formula_element())
        doc["elements"][-1]["content"]["latex"] = None
        doc["elements"][-1]["content"]["recognized_formula"] = None
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 15. figure asset completeness
    def test_inv15_asset_zero_pixels(self):
        doc = _baseline_doc()
        doc["elements"].append(_figure_element())
        doc["elements"][-1]["content"]["asset"]["pixel_width"] = 0
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv15_asset_bad_sha(self):
        doc = _baseline_doc()
        doc["elements"].append(_figure_element())
        doc["elements"][-1]["content"]["asset"]["sha256"] = "zz"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 16. provenance engine_id resolves
    def test_inv16_unknown_engine(self):
        doc = _baseline_doc()
        doc["elements"][0]["provenance"][0]["engine_id"] = "ghost-1"
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    def test_inv16_confidence_out_of_range(self):
        doc = _baseline_doc()
        doc["elements"][0]["provenance"][0]["confidence"] = 1.5
        with self.assertRaises(SchemaError):
            CanonicalDocument.from_dict(doc)

    # 17. no QA/review/projection fields -> top-level unknown rejected
    def test_inv17_no_qa_fields(self):
        self._assert_invalid(lambda d: d.__setitem__("publishable", True))


class RoundtripAndDigestTest(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        doc = _baseline_doc()
        doc["elements"].extend([_table_element(), _formula_element(), _figure_element()])
        model = CanonicalDocument.from_dict(doc)
        out = model.to_dict()
        again = CanonicalDocument.from_dict(out)
        self.assertEqual(again.canonical_id, model.canonical_id)
        self.assertEqual(len(again.elements), 4)

    def test_metadata_fingerprint_changes_only_with_metadata(self):
        doc = _baseline_doc()
        model = CanonicalDocument.from_dict(doc)
        content_before = model.content_digest_payload()
        meta_before = model.metadata_digest_payload()
        full_before = model.canonical_digest_payload()
        # mutate metadata only
        doc["metadata"]["title"] = "改标题"
        model2 = CanonicalDocument.from_dict(doc)
        self.assertEqual(content_before, model2.content_digest_payload())  # content cache key unchanged
        self.assertNotEqual(meta_before, model2.metadata_digest_payload())
        self.assertNotEqual(full_before, model2.canonical_digest_payload())

    def test_content_digest_changes_with_elements(self):
        doc = _baseline_doc()
        model = CanonicalDocument.from_dict(doc)
        content_before = model.content_digest_payload()
        doc["elements"][0]["content"]["text"] = "降雨的部分过程。"
        model2 = CanonicalDocument.from_dict(doc)
        self.assertNotEqual(content_before, model2.content_digest_payload())


class LegacyShimTest(unittest.TestCase):
    def test_legacy_document_still_constructs(self):
        from src.domain.canonical_document import LegacyCanonicalDocument, LegacyCanonicalPage, LegacyCanonicalElement

        page = LegacyCanonicalPage(
            sample_id="s1", physical_page=1, page_index=0, display_page_label=None,
            width=400.0, height=600.0, rotation=0, extraction_route="native",
            parser_name="pymupdf", parser_profile="default", decision_status="approved",
            decision_reasons=[], publishable=True, text="x",
            elements=[LegacyCanonicalElement(
                element_id="e1", type="text", text="x", normalized_text="x",
                page_index=0, bbox=[0, 0, 1, 1],
            )],
            tables=[], raw_artifact_ref="ref",
        )
        doc = LegacyCanonicalDocument(
            schema_version="v1", scope="x", asset_id="a", document_version_id="d",
            file_name="x.pdf", sha256=SHA, source_uri=None, processing_run_id="r",
            config_hash=CONFIG_SHA, pages=[page],
        )
        doc.validate()  # no raise
        self.assertEqual(doc.to_dict()["sha256"], SHA)


if __name__ == "__main__":
    unittest.main()
