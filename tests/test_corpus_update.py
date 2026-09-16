from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.application.corpus_update import CorpusUpdateService, build_source_catalog


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
    def test_update_plan_exposes_legacy_and_completeness_uncertainty(self):
        from scripts.update_corpus import plan

        row = self.row(name="甲.pdf", rel="data/raw/资料/甲.pdf", data=b"pdf-a")
        self.write_inventory([row])
        result = plan(self.root)
        self.assertEqual(result["source_evidence_status_counts"], {"legacy_unverified": 1})
        self.assertEqual(result["completeness_status_counts"], {"unverified": 1})

    def test_hash_bound_source_evidence_is_used_without_repromoting_legacy_flags(self):
        row = self.row(name="甲.pdf", rel="data/raw/资料/甲.pdf", data=b"pdf-a")
        self.write_inventory([row])
        record = {
            "schema_version": "1", "file_sha256": row["sha256"],
            "aliases": [{"file_name": "old-name.pdf", "rel_path": "data/raw/old-name.pdf"}],
            "observations": [{
                "field": "source_authority", "value": "核验机关",
                "origin_kind": "external_verified", "status": "verified",
                "evidence_ref": {"kind": "url", "url": "https://example.org/notice"},
                "checked_at": "2026-09-16T00:00:00+00:00",
                "verification_method": "manual_field_comparison",
            }],
        }
        registry = self.root / "data/registry/source_evidence.jsonl"
        registry.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        catalog = build_source_catalog(self.root)
        self.assertEqual(catalog.assets[0].metadata["source_authority"], "核验机关")
        self.assertEqual(catalog.assets[0].metadata["source_evidence_status"], "has_verified_fields")
        before = catalog.fingerprint_payload()
        record["observations"][0]["value"] = "另一个核验机关"
        registry.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertNotEqual(before, build_source_catalog(self.root).fingerprint_payload())
        changed = self.row(name="甲.pdf", rel="data/raw/资料/甲.pdf", data=b"changed", source_review="official_fulltext_verified")
        self.write_inventory([changed])
        asset = build_source_catalog(self.root).assets[0]
        self.assertEqual(asset.metadata["source_review"], "needs_review")
        self.assertEqual(asset.metadata["source_evidence_status"], "unverified")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data" / "raw" / "_official_verification").mkdir(
            parents=True
        )
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

    def test_catalog_prefers_official_file_but_keeps_readable_identity(self):
        data = b"same-pdf"
        rows = [
            self.row(
                name="规范中文名.pdf",
                rel="data/raw/资料/规范中文名.pdf",
                data=data,
                std_no="GB 1-2026",
            ),
            self.row(
                name="official-copy.pdf",
                rel="data/raw/_official_verification/official-copy.pdf",
                data=data,
                inventory_status="exact_duplicate",
            ),
        ]
        self.write_inventory(rows)
        reviews = self.root / "data" / "registry" / "document_reviews.csv"
        reviews.write_text(
            "file_name,official_source_uri,source_authority,source_review,publication_date,effective_date,effective_status,local_file_match,selection_status,review_note\n"
            "规范中文名.pdf,https://example.gov.cn/a.pdf,发布机关,official_fulltext_verified,2026-01-01,2026-02-01,current,sha256_match,approved_formal,\n",
            encoding="utf-8-sig",
        )

        catalog = build_source_catalog(self.root)

        self.assertEqual(catalog.source_file_count, 2)
        self.assertEqual(catalog.unique_content_count, 1)
        asset = catalog.assets[0]
        self.assertEqual(
            asset.canonical_rel_path,
            "data/raw/_official_verification/official-copy.pdf",
        )
        self.assertEqual(asset.display_file_name, "规范中文名.pdf")
        self.assertEqual(asset.metadata["source_review"], "official_fulltext_verified")
        self.assertEqual(asset.metadata["official_source_uri"], "https://example.gov.cn/a.pdf")
        self.assertEqual(len(asset.source_files), 2)

    def test_unchanged_content_is_reused_and_only_new_hash_is_processed(self):
        first = self.row(
            name="甲.pdf",
            rel="data/raw/资料/甲.pdf",
            data=b"pdf-a",
            pages=2,
        )
        second = self.row(
            name="乙.pdf",
            rel="data/raw/资料/乙.pdf",
            data=b"pdf-b",
            pages=1,
        )
        self.write_inventory([first, second])
        extractor = FakePageExtractor()
        service = CorpusUpdateService(
            self.root,
            page_extractor=extractor,
            worker_count=2,
            page_extractor_factory=lambda: FakePageExtractor(extractor.calls),
        )

        initial = service.build()

        self.assertEqual(initial["source_file_count"], 2)
        self.assertEqual(initial["unique_content_count"], 2)
        self.assertEqual(initial["processed_content_count"], 2)
        self.assertEqual(initial["reused_content_count"], 0)
        self.assertEqual(initial["page_status_counts"], {"approved": 3})
        self.assertEqual(len(extractor.calls), 3)
        evidence_path = self.root / initial["evidence_units"]
        evidence = json.loads(evidence_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(
            evidence["document_metadata"]["source_review"], "needs_review"
        )

        initial_manifest_path = (
            self.root / "data" / "registry" / f"{initial['corpus_version']}.json"
        )
        initial_manifest_bytes = initial_manifest_path.read_bytes()

        unchanged = service.build()

        self.assertEqual(initial_manifest_path.read_bytes(), initial_manifest_bytes)
        self.assertEqual(unchanged["processed_content_count"], 0)
        for cache in (self.root / "data/canonical/assets").glob("*.json"):
            cache.unlink()

        rebuilt_from_pages = service.build()

        self.assertEqual(len(extractor.calls), 3)
        self.assertEqual(rebuilt_from_pages["unique_content_count"], 2)
        self.assertEqual(unchanged["reused_content_count"], 2)
        self.assertEqual(len(extractor.calls), 3)

        first["source_review"] = "official_fulltext_verified"
        first["official_source_uri"] = "https://example.gov.cn/a.pdf"
        self.write_inventory([first, second])

        metadata_only = service.build()

        self.assertEqual(metadata_only["processed_content_count"], 0)
        self.assertEqual(metadata_only["reused_content_count"], 2)
        self.assertEqual(len(extractor.calls), 3)
        refreshed_evidence = [
            json.loads(line)
            for line in (self.root / metadata_only["evidence_units"])
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        official = next(
            unit
            for unit in refreshed_evidence
            if unit["document_metadata"]["official_source_uri"]
        )
        self.assertEqual(
            official["document_metadata"]["source_review"],
            "official_fulltext_verified",
        )
        self.assertEqual(len(metadata_only["document_version_ids"]), 2)
        self.assertEqual(
            len(metadata_only["chunk_ids"]), metadata_only["chunk_count"]
        )

        third = self.row(
            name="丙.pdf",
            rel="data/raw/资料/丙.pdf",
            data=b"pdf-c",
            pages=1,
        )
        self.write_inventory([first, second, third])

        incremental = service.build()

        self.assertEqual(incremental["processed_content_count"], 1)
        self.assertEqual(incremental["reused_content_count"], 2)
        self.assertEqual(len(extractor.calls), 4)
        pointer = json.loads(
            (self.root / "data" / "registry" / "corpus-candidate.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pointer["corpus_version"], incremental["corpus_version"])

    def test_artifact_pipeline_profile_changes_the_corpus_version(self):
        source = self.row(
            name="甲.pdf",
            rel="data/raw/资料/甲.pdf",
            data=b"pdf-a",
            pages=1,
        )
        self.write_inventory([source])
        service = CorpusUpdateService(
            self.root,
            page_extractor=FakePageExtractor(),
        )

        first = service.build()
        with mock.patch(
            "src.application.corpus_update._CORPUS_ARTIFACT_PROFILE",
            "test-artifacts-v2",
        ):
            second = service.build()

        self.assertNotEqual(first["corpus_version"], second["corpus_version"])


if __name__ == "__main__":
    unittest.main()
