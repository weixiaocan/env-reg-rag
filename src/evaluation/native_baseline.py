"""Run the M2 PyMuPDF baseline against the fixed representative pages."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import platform
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.ingestion.native_pdf import NativePdfParser


BASELINE_CONFIG_VERSION = "1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _normalized_for_exact_anchor(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _evaluate_anchors(text: str, anchors: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized_text = _normalized_for_exact_anchor(text)
    checks = []
    for anchor in anchors:
        expected = anchor["proposed_truth"]
        matched = _normalized_for_exact_anchor(expected) in normalized_text
        checks.append(
            {
                "anchor_id": anchor["anchor_id"],
                "anchor_type": anchor["anchor_type"],
                "matched": matched,
                "comparison": "normalized_exact_substring",
            }
        )
    return {
        "anchor_count": len(checks),
        "matched_count": sum(check["matched"] for check in checks),
        "all_matched": all(check["matched"] for check in checks) if checks else None,
        "checks": checks,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_native_baseline(
    project_root: Path,
    output_dir: Path,
    *,
    sample_ids: set[str] | None = None,
    overwrite: bool = False,
    artifact_prefix: str = "m2-native-pymupdf-1.28.2-v1",
) -> dict[str, Any]:
    """Extract fixed M2 pages and persist traceable raw/canonical page evidence."""

    project_root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_dir():
        raise FileNotFoundError(f"baseline output directory does not exist: {output_dir}")
    metadata_path = output_dir / f"{artifact_prefix}-run-metadata.json"
    pages_path = output_dir / f"{artifact_prefix}-pages.jsonl"
    summary_path = output_dir / f"{artifact_prefix}-summary.json"
    if metadata_path.exists() and not overwrite:
        raise FileExistsError(
            f"baseline output already exists: {metadata_path}; "
            "choose a new artifact_prefix or use overwrite"
        )

    registry = project_root / "data" / "registry"
    manifest = json.loads(
        (registry / "experiment-sample-v0.json").read_text(encoding="utf-8-sig")
    )
    if manifest.get("status") != "approved_for_experiment":
        raise ValueError("experiment manifest is not approved_for_experiment")
    approved_anchors = json.loads(
        (registry / "m2-anchor-review-v0.json").read_text(encoding="utf-8-sig")
    )
    if approved_anchors.get("status") != "human_approved":
        raise ValueError("M2 anchors must be human_approved before baseline execution")

    inventory = {
        row["file_name"]: row for row in _read_csv(registry / "inventory.csv")
    }
    samples = _read_csv(registry / "m2-page-sample-v0.csv")
    if sample_ids is not None:
        samples = [row for row in samples if row["sample_id"] in sample_ids]
        found = {row["sample_id"] for row in samples}
        missing = sample_ids - found
        if missing:
            raise ValueError(f"unknown sample_ids: {sorted(missing)}")

    anchors_by_sample: dict[str, list[dict[str, Any]]] = {}
    for anchor in approved_anchors["anchors"]:
        anchors_by_sample.setdefault(anchor["sample_id"], []).append(anchor)

    parser = NativePdfParser()
    pages = []
    failures = []
    verified_documents: dict[str, str] = {}
    for sample in samples:
        file_name = sample["file_name"]
        inventory_row = inventory[file_name]
        source = project_root / inventory_row["rel_path"]
        approved_hash = inventory_row["sha256"].lower()
        if file_name not in verified_documents:
            actual_hash = _sha256(source)
            if actual_hash != approved_hash:
                raise ValueError(f"local PDF SHA-256 changed: {file_name}")
            verified_documents[file_name] = actual_hash

        started = time.perf_counter()
        try:
            page = parser.parse_page(
                source,
                physical_page=int(sample["pdf_page"]),
            )
            page["sample_id"] = sample["sample_id"]
            page["file_name"] = file_name
            page["document_sha256"] = approved_hash
            page["route_hypothesis"] = sample["route_hypothesis"]
            page["content_type"] = sample["content_type"]
            page["anchor_evaluation"] = _evaluate_anchors(
                page["text"], anchors_by_sample.get(sample["sample_id"], [])
            )
            page["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            pages.append(page)
        except Exception as exc:  # Failures are evidence and must not be silently dropped.
            failures.append(
                {
                    "sample_id": sample["sample_id"],
                    "file_name": file_name,
                    "physical_page": int(sample["pdf_page"]),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    quality_counts = Counter(page["quality"]["status"] for page in pages)
    anchor_checks = [
        check
        for page in pages
        for check in page["anchor_evaluation"]["checks"]
    ]
    summary = {
        "page_count": len(pages),
        "failed_page_count": len(failures),
        "surface_quality": dict(sorted(quality_counts.items())),
        "anchor_count": len(anchor_checks),
        "matched_anchor_count": sum(check["matched"] for check in anchor_checks),
        "unmatched_anchor_ids": [
            check["anchor_id"] for check in anchor_checks if not check["matched"]
        ],
        "elapsed_ms_total": round(sum(page["elapsed_ms"] for page in pages), 3),
    }
    config = {
        "config_version": BASELINE_CONFIG_VERSION,
        "text_comparison": "remove_whitespace_then_exact_substring",
        "coordinate_origin": "top_left",
        "page_numbering": "physical_page_1_based_and_page_index_0_based",
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    metadata = {
        "processing_run_id": f"m2-native-{importlib.metadata.version('PyMuPDF')}-{config_hash[:12]}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_name": parser.name,
        "parser_version": importlib.metadata.version("PyMuPDF"),
        "python_version": platform.python_version(),
        "input_manifest_id": manifest["manifest_id"],
        "input_manifest_sha256": _sha256(registry / "experiment-sample-v0.json"),
        "anchor_set_sha256": _sha256(registry / "m2-anchor-review-v0.json"),
        "sample_set_sha256": _sha256(registry / "m2-page-sample-v0.csv"),
        "config": config,
        "config_sha256": config_hash,
        "documents": [
            {"file_name": name, "sha256": sha256}
            for name, sha256 in verified_documents.items()
        ],
    }
    _write_jsonl(pages_path, pages)
    _write_json(metadata_path, metadata)
    _write_json(summary_path, {**summary, "failures": failures})
    return {"metadata": metadata, "summary": summary, "pages": pages, "failures": failures}
