"""Regression: assemble must not mix content and metadata digest IDs."""
from __future__ import annotations

import unittest

from src.application.canonical_validation import apply_computed_ids


class ApplyComputedIdsTests(unittest.TestCase):
    def test_fields_stay_distinct(self):
        doc = {}
        ids = {
            "canonical_id": "canonical-sha256:" + "1" * 64,
            "canonical_content_id": "canonical-content-sha256:" + "2" * 64,
            "metadata_fingerprint": "metadata-sha256:" + "3" * 64,
        }
        apply_computed_ids(doc, ids)
        self.assertEqual(doc["canonical_id"], ids["canonical_id"])
        self.assertEqual(doc["canonical_content_id"], ids["canonical_content_id"])
        self.assertEqual(doc["metadata_fingerprint"], ids["metadata_fingerprint"])
        self.assertNotEqual(doc["canonical_content_id"], doc["metadata_fingerprint"])
        self.assertTrue(doc["canonical_content_id"].startswith("canonical-content-sha256:"))
        self.assertTrue(doc["metadata_fingerprint"].startswith("metadata-sha256:"))


if __name__ == "__main__":
    unittest.main()
