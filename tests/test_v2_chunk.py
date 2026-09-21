"""Unit tests for the V2 Chunk domain model (plan 2.1 acceptance).

Covers:
- chunk_id digest determinism (same inputs -> same id; any input change -> diff)
- unknown-field rejection at every nesting level
- provenance-field completeness (chunk_id format, text/element_ids/source_spans
  non-empty, round-trip, qdrant payload shape)
"""

from __future__ import annotations

import copy
import unittest

from src.domain.canonical_document import SchemaError
from src.domain.v2_chunk import (
    CHUNK_SCHEMA_VERSION,
    DEFAULT_PROJECTION_VERSION,
    ChunkSourceSpan,
    V2Chunk,
    compute_chunk_id,
)

CONTENT_ID = "canonical-content-sha256:" + "b4" * 32
CANONICAL_ID = "canonical-sha256:" + "04" * 32


def _baseline_chunk() -> dict:
    return {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_id": "chunk_" + "0" * 32,
        "canonical_id": CANONICAL_ID,
        "canonical_content_id": CONTENT_ID,
        "element_ids": ["e000063", "e000064"],
        "text": "5.3 水质特征因子的选择\n水质特征因子应能区分污水与雨水。",
        "source_spans": [
            {"physical_page": 13, "bbox": [10.0, 10.0, 200.0, 50.0]},
            {"physical_page": 13, "bbox": [10.0, 50.0, 200.0, 90.0]},
        ],
        "projection_version": DEFAULT_PROJECTION_VERSION,
        "metadata": {
            "standard_number": "T/CECS 758-2020",
            "document_kind": "standard",
            "jurisdictions": ["全国"],
            "effective_status": "current",
            "element_types": ["text"],
            "physical_pages": [13],
            "section_path": [{"label": "5.3", "title": "水质特征因子的选择"}],
        },
    }


class ChunkIdDigestTest(unittest.TestCase):
    def test_deterministic_same_inputs(self):
        a = compute_chunk_id(
            canonical_content_id=CONTENT_ID,
            element_ids=["e1", "e2"],
            projection_version=DEFAULT_PROJECTION_VERSION,
            text="示例条文",
        )
        b = compute_chunk_id(
            canonical_content_id=CONTENT_ID,
            element_ids=["e1", "e2"],
            projection_version=DEFAULT_PROJECTION_VERSION,
            text="示例条文",
        )
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("chunk_"))
        self.assertEqual(len(a), len("chunk_") + 32)

    def test_text_change_changes_id(self):
        base = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="甲",
        )
        diff = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="乙",
        )
        self.assertNotEqual(base, diff)

    def test_element_ids_order_matters(self):
        a = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1", "e2"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="t",
        )
        b = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e2", "e1"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="t",
        )
        self.assertNotEqual(a, b)

    def test_metadata_only_change_keeps_id(self):
        """canonical_content_id (not canonical_id) drives the id (design §4.3)."""
        same = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="t",
        )
        # A different canonical_id (audit snapshot) with the SAME content id
        # must yield the SAME chunk id -> metadata change does not re-chunk.
        with_other_audit = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version=DEFAULT_PROJECTION_VERSION, text="t",
        )
        self.assertEqual(same, with_other_audit)

    def test_projection_version_change_changes_id(self):
        a = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version="chunk-text/v1", text="t",
        )
        b = compute_chunk_id(
            canonical_content_id=CONTENT_ID, element_ids=["e1"],
            projection_version="chunk-text/v2", text="t",
        )
        self.assertNotEqual(a, b)


class UnknownFieldRejectionTest(unittest.TestCase):
    def _assert_unknown_rejected(self, mutate):
        chunk = _baseline_chunk()
        mutate(chunk)
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_top_level(self):
        self._assert_unknown_rejected(lambda c: c.__setitem__("qa_status", "pass"))

    def test_source_span(self):
        self._assert_unknown_rejected(
            lambda c: c["source_spans"][0].__setitem__("span_id", "s1")
        )

    def test_missing_metadata_key(self):
        chunk = _baseline_chunk()
        del chunk["metadata"]
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)


class ProvenanceCompletenessTest(unittest.TestCase):
    def test_roundtrip(self):
        chunk = V2Chunk.from_dict(_baseline_chunk())
        out = chunk.to_dict()
        again = V2Chunk.from_dict(out)
        self.assertEqual(again.chunk_id, chunk.chunk_id)
        self.assertEqual(again.element_ids, chunk.element_ids)
        self.assertEqual(again.canonical_content_id, chunk.canonical_content_id)

    def test_chunk_id_matches_compute(self):
        data = _baseline_chunk()
        data["chunk_id"] = compute_chunk_id(
            canonical_content_id=data["canonical_content_id"],
            element_ids=data["element_ids"],
            projection_version=data["projection_version"],
            text=data["text"],
        )
        chunk = V2Chunk.from_dict(data)
        self.assertEqual(chunk.chunk_id, data["chunk_id"])

    def test_bad_chunk_id_prefix_rejected(self):
        chunk = _baseline_chunk()
        chunk["chunk_id"] = "ev_" + "0" * 32
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_bad_chunk_id_hex_rejected(self):
        chunk = _baseline_chunk()
        chunk["chunk_id"] = "chunk_" + "z" * 32
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_empty_text_rejected(self):
        chunk = _baseline_chunk()
        chunk["text"] = "   "
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_empty_element_ids_rejected(self):
        chunk = _baseline_chunk()
        chunk["element_ids"] = []
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_duplicate_element_ids_rejected(self):
        chunk = _baseline_chunk()
        chunk["element_ids"] = ["e1", "e1"]
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_empty_source_spans_rejected(self):
        chunk = _baseline_chunk()
        chunk["source_spans"] = []
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_bad_bbox_rejected(self):
        chunk = _baseline_chunk()
        chunk["source_spans"][0]["bbox"] = [100.0, 10.0, 50.0, 90.0]  # x0 > x1
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_bad_page_rejected(self):
        chunk = _baseline_chunk()
        chunk["source_spans"][0]["physical_page"] = 0
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)

    def test_wrong_schema_version_rejected(self):
        chunk = _baseline_chunk()
        chunk["schema_version"] = "v2.chunk/2.0"
        with self.assertRaises(SchemaError):
            V2Chunk.from_dict(chunk)


class QdrantPayloadTest(unittest.TestCase):
    def test_payload_has_chunk_id_and_text_at_top(self):
        chunk = V2Chunk.from_dict(_baseline_chunk())
        payload = chunk.to_qdrant_payload()
        self.assertEqual(payload["chunk_id"], chunk.chunk_id)
        self.assertEqual(payload["text"], chunk.text)
        # metadata folded to top level (no nested "metadata" key) so build()'s
        # {**chunk, **chunk.get("metadata", {})} stores everything flat.
        self.assertNotIn("metadata", payload)
        self.assertEqual(payload["standard_number"], "T/CECS 758-2020")
        self.assertEqual(payload["physical_pages"], [13])
        self.assertEqual(payload["element_ids"], ["e000063", "e000064"])
        self.assertEqual(payload["canonical_content_id"], CONTENT_ID)

    def test_chunk_source_span_dataclass(self):
        span = ChunkSourceSpan.from_dict({"physical_page": 5, "bbox": [0.0, 0.0, 1.0, 1.0]})
        self.assertEqual(span.physical_page, 5)
        self.assertEqual(span.bbox, [0.0, 0.0, 1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
