"""Regression: triage.json must track current catalog coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.evaluation.formula_checkpoint_audit import (
    resolve_formula_config_hash,
    write_triage_report,
)


def _config_hash(model_checksums, package_versions):
    provenance = {
        "profile": "layout-formula-v1:200dpi:score0.5:padding2point",
        "model_checksums": model_checksums,
        "package_versions": package_versions,
    }
    return hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()


def _write_checkpoint(directory: Path, sha: str, page: int, config_hash: str, *,
                      model_checksums, package_versions, region_index: int = 0) -> None:
    region = {
        "candidate_id": f"layout-{sha[:16]}-{page}-{region_index}",
        "sha256": sha,
        "physical_page": page,
        "bbox": [1.0, 2.0, 3.0, 4.0],
        "crop_bbox": [1.0, 2.0, 3.0, 4.0],
        "raw_latex": "a=b",
        "score": 0.9,
    }
    result = {
        "sha256": sha,
        "coordinate_system": "page_points_top_left",
        "profile": "layout-formula-v1:200dpi:score0.5",
        "model_checksums": model_checksums,
        "package_versions": package_versions,
        "pages": [{"physical_page": page, "regions": [region]}],
    }
    payload = {
        "sha256": sha,
        "physical_page": page,
        "config_hash": config_hash,
        "status": "completed",
        "result": result,
    }
    (directory / f"{sha}-{page}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


class FormulaTriageRefreshTests(unittest.TestCase):
    def test_write_triage_report_updates_document_and_page_counts(self):
        model_checksums = {"layout": "abc", "ocr": "def"}
        package_versions = {"paddle": "1.0"}
        config_hash = _config_hash(model_checksums, package_versions)
        run_id = config_hash[:16]
        sha_a = "a" * 64
        sha_b = "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / run_id
            directory.mkdir()
            _write_checkpoint(directory, sha_a, 1, config_hash,
                              model_checksums=model_checksums,
                              package_versions=package_versions)
            _write_checkpoint(directory, sha_b, 1, config_hash,
                              model_checksums=model_checksums,
                              package_versions=package_versions, region_index=0)
            # Stale triage from a smaller catalog (1 doc / 1 page).
            stale = {
                "schema_version": "1",
                "config_hash": config_hash,
                "document_count": 1,
                "page_count": 1,
                "region_count": 0,
                "regions": [],
                "review_status": "pending_review",
                "checkpoint_manifest_sha256": "0" * 64,
                "publishable": False,
                "can_use_for_calculation": False,
                "counts": {},
            }
            (directory / "triage.json").write_text(json.dumps(stale), encoding="utf-8")
            (directory / "summary.json").write_text(
                json.dumps({"config_hash": config_hash, "status": "finished_pending_review"}),
                encoding="utf-8",
            )
            jobs = [(sha_a, 1), (sha_b, 1)]
            report = write_triage_report(directory, jobs, config_hash, document_count=2)
            self.assertEqual(report["document_count"], 2)
            self.assertEqual(report["page_count"], 2)
            self.assertEqual(report["region_count"], 2)
            saved = json.loads((directory / "triage.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["document_count"], 2)
            self.assertEqual(saved["page_count"], 2)
            self.assertEqual(resolve_formula_config_hash(directory, run_id), config_hash)


if __name__ == "__main__":
    unittest.main()
