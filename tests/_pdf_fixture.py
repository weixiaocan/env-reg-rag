"""Shared PDF-corpus fixture for ingestion / OCR tooling tests.

Extracted from the former ``tests/test_corpus_update.py`` (deleted with the V1
retrieval chain). Provides only the page-extractor fake + temp-project setup
needed by tests that exercise ``CorpusUpdateService`` page-cache machinery,
``build_source_catalog``, table/formula recognition, and document relations.
No V1 retrieval-chain tests live here.
"""

from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path


HEADERS = (
    "file_name",
    "rel_path",
    "sha256",
    "size_mb",
    "pages",
    "text_coverage",
    "extraction_route_candidate",
    "std_no",
    "metadata_source",
    "document_kind",
    "jurisdiction",
    "official_source_uri",
    "source_review",
    "effective_status",
    "same_standard_as",
    "exact_duplicate_of",
    "inventory_status",
    "note",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakePageExtractor:
    config_id = "fake-parser-v1"

    def __init__(self, calls=None) -> None:
        self.calls: list[tuple[str, int]] = calls if calls is not None else []

    def extract(self, asset, *, physical_page: int):
        self.calls.append((asset.sha256, physical_page))
        page_index = physical_page - 1
        text = f"{asset.display_file_name} 第{physical_page}页规范内容"
        return {
            "physical_page": physical_page,
            "page_index": page_index,
            "display_page_label": None,
            "width": 595.0,
            "height": 842.0,
            "rotation": 0,
            "extraction_route": "native",
            "parser_name": "fake-parser",
            "parser_profile": "test",
            "decision_status": "approved",
            "decision_reasons": [],
            "publishable": True,
            "text": text,
            "elements": [
                {
                    "element_id": f"p{page_index:04d}-b0000",
                    "type": "text",
                    "text": text,
                    "normalized_text": text,
                    "page_index": page_index,
                    "bbox": [10.0, 10.0, 500.0, 30.0],
                    "coordinate_origin": "top_left",
                    "reading_order": 0,
                    "heading_path": [],
                    "clause_path": [],
                    "parent_id": None,
                    "children_ids": [],
                    "confidence": None,
                }
            ],
            "tables": [],
            "raw_artifact_ref": "",
            "coordinate_normalizations": [],
        }


class CorpusUpdateTest(unittest.TestCase):
    """Base fixture: temp project root with registry + raw dirs."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "raw" / "_official_verification").mkdir(parents=True)
        (self.root / "data" / "raw" / "资料").mkdir(parents=True)
        (self.root / "data" / "registry").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_inventory(self, rows: list[dict[str, str]]) -> None:
        path = self.root / "data" / "registry" / "inventory.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)

    def row(self, *, name: str, rel: str, data: bytes, pages: int = 1, **extra):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        row = {header: "" for header in HEADERS}
        row.update(
            {
                "file_name": name,
                "rel_path": rel,
                "sha256": digest(data),
                "size_mb": "0.01",
                "pages": str(pages),
                "document_kind": "standard",
                "jurisdiction": "全国",
                "source_review": "needs_review",
                "effective_status": "unknown",
                "inventory_status": "candidate",
            }
        )
        row.update(extra)
        return row
