"""Run the M2 PP-StructureV3 baseline on OCR degradation candidates."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import psutil

from src.ingestion.ocr_pdf import OcrPdfParser, StructureOcrEngine
from src.evaluation.table_assertions import evaluate_table_assertion


OCR_ROUTES = {"full_ocr_candidate", "hybrid_candidate"}
OCR_PROFILES = {"text_layout_light", "table_structure_heavy"}
BASELINE_CONFIG_VERSION = "2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _evaluate_anchors(text: str, anchors: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized_text = re.sub(r"\s+", "", text)
    checks = []
    for anchor in anchors:
        matched = re.sub(r"\s+", "", anchor["proposed_truth"]) in normalized_text
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
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_ocr_baseline(
    project_root: Path,
    output_dir: Path,
    *,
    engine: StructureOcrEngine | None = None,
    sample_ids: set[str] | None = None,
    overwrite: bool = False,
    artifact_prefix: str = "m2-ocr-ppstructurev3-3.7.0-v1",
    dpi: int = 200,
    table_recognition: bool | None = None,
    ocr_profile: str | None = None,
    resume: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """OCR fixed M2 degradation candidates and persist traceable page evidence."""

    project_root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve()
    if not output_dir.is_dir():
        raise FileNotFoundError(f"baseline output directory does not exist: {output_dir}")
    metadata_path = output_dir / f"{artifact_prefix}-run-metadata.json"
    pages_path = output_dir / f"{artifact_prefix}-pages.jsonl"
    summary_path = output_dir / f"{artifact_prefix}-summary.json"
    checkpoint_path = output_dir / f"{artifact_prefix}-checkpoint.jsonl"
    if metadata_path.exists() and not overwrite:
        raise FileExistsError(
            f"baseline output already exists: {metadata_path}; "
            "choose a new artifact_prefix or use overwrite"
        )

    registry = project_root / "data" / "registry"
    route_plan_path = registry / "m2-ocr-route-plan-v1.csv"
    route_plan = _read_csv(route_plan_path)
    if ocr_profile is not None and ocr_profile not in OCR_PROFILES:
        raise ValueError(f"unknown ocr_profile: {ocr_profile}")
    expected_table_recognition = ocr_profile == "table_structure_heavy"
    if ocr_profile is not None:
        if table_recognition is None:
            table_recognition = expected_table_recognition
        elif table_recognition != expected_table_recognition:
            raise ValueError(
                f"ocr_profile {ocr_profile} conflicts with "
                f"table_recognition={table_recognition}"
            )
    elif table_recognition is None:
        table_recognition = True
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
    table_assertion_path = registry / "m2-table-anchor-assertions-v1.json"
    table_assertions = json.loads(table_assertion_path.read_text(encoding="utf-8-sig"))
    table_assertion_by_sample = {
        item["sample_id"]: item for item in table_assertions["anchors"]
    }

    inventory = {row["file_name"]: row for row in _read_csv(registry / "inventory.csv")}
    all_samples = _read_csv(registry / "m2-page-sample-v0.csv")
    if sample_ids is not None:
        known = {row["sample_id"] for row in all_samples}
        missing = sample_ids - known
        if missing:
            raise ValueError(f"unknown sample_ids: {sorted(missing)}")
        all_samples = [row for row in all_samples if row["sample_id"] in sample_ids]
    samples = [row for row in all_samples if row["route_hypothesis"] in OCR_ROUTES]
    if ocr_profile is not None:
        profile_sample_ids = {
            row["sample_id"]
            for row in route_plan
            if row["ocr_profile"] == ocr_profile
        }
        samples = [row for row in samples if row["sample_id"] in profile_sample_ids]

    checkpoint_identity = {
        "schema_version": 1,
        "sample_ids": [row["sample_id"] for row in samples],
        "ocr_profile": ocr_profile,
        "dpi": dpi,
        "table_recognition": table_recognition,
        "manifest_sha256": _sha256(registry / "experiment-sample-v0.json"),
        "anchor_set_sha256": _sha256(registry / "m2-anchor-review-v0.json"),
        "table_assertion_set_sha256": _sha256(table_assertion_path),
        "sample_set_sha256": _sha256(registry / "m2-page-sample-v0.csv"),
        "route_plan_sha256": _sha256(route_plan_path),
    }
    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    completed_sample_ids: set[str] = set()
    if overwrite and checkpoint_path.exists():
        checkpoint_path.unlink()
    if checkpoint_path.exists():
        if not resume:
            raise FileExistsError(
                f"unfinished checkpoint exists: {checkpoint_path}; use resume or overwrite"
            )
        records = [
            json.loads(line)
            for line in checkpoint_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not records or records[0] != {
            "record_type": "run_header",
            "identity": checkpoint_identity,
        }:
            raise ValueError("checkpoint identity does not match this OCR run")
        for record in records[1:]:
            if record["record_type"] == "page":
                pages.append(record["payload"])
            elif record["record_type"] == "failure":
                failures.append(record["payload"])
            else:
                raise ValueError(f"unknown checkpoint record: {record['record_type']}")
            completed_sample_ids.add(record["sample_id"])
    else:
        _append_checkpoint(
            checkpoint_path,
            {"record_type": "run_header", "identity": checkpoint_identity},
        )

    anchors_by_sample: dict[str, list[dict[str, Any]]] = {}
    for anchor in approved_anchors["anchors"]:
        anchors_by_sample.setdefault(anchor["sample_id"], []).append(anchor)

    parser = OcrPdfParser(
        engine=engine,
        dpi=dpi,
        table_recognition=table_recognition,
    )
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

        if sample["sample_id"] in completed_sample_ids:
            continue

        started = time.perf_counter()
        try:
            page = parser.parse_page(source, physical_page=int(sample["pdf_page"]))
            page["extraction_route"] = (
                "hybrid_ocr"
                if sample["route_hypothesis"] == "hybrid_candidate"
                else "full_ocr"
            )
            page["sample_id"] = sample["sample_id"]
            page["file_name"] = file_name
            page["document_sha256"] = approved_hash
            page["route_hypothesis"] = sample["route_hypothesis"]
            page["content_type"] = sample["content_type"]
            page["anchor_evaluation"] = _evaluate_anchors(
                page["text"], anchors_by_sample.get(sample["sample_id"], [])
            )
            table_assertion = table_assertion_by_sample.get(sample["sample_id"])
            page["table_assertion_evaluation"] = (
                evaluate_table_assertion(page, table_assertion)
                if table_assertion is not None
                else None
            )
            page["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            memory = psutil.Process().memory_info()
            page["resource_usage"] = {
                "rss_mb": round(memory.rss / (1024 * 1024), 3),
                "peak_working_set_mb": round(
                    getattr(memory, "peak_wset", memory.rss) / (1024 * 1024), 3
                ),
            }
            pages.append(page)
            _append_checkpoint(
                checkpoint_path,
                {
                    "record_type": "page",
                    "sample_id": sample["sample_id"],
                    "payload": page,
                },
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "sample_id": sample["sample_id"],
                        "outcome": "page",
                        "completed": len(pages) + len(failures),
                        "total": len(samples),
                    }
                )
        except Exception as exc:
            failure = {
                "sample_id": sample["sample_id"],
                "file_name": file_name,
                "physical_page": int(sample["pdf_page"]),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            failures.append(failure)
            _append_checkpoint(
                checkpoint_path,
                {
                    "record_type": "failure",
                    "sample_id": sample["sample_id"],
                    "payload": failure,
                },
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "sample_id": sample["sample_id"],
                        "outcome": "failure",
                        "completed": len(pages) + len(failures),
                        "total": len(samples),
                    }
                )

    quality_counts = Counter(page["quality"]["status"] for page in pages)
    anchor_checks = [
        check for page in pages for check in page["anchor_evaluation"]["checks"]
    ]
    table_evaluations = [
        page["table_assertion_evaluation"]
        for page in pages
        if page["table_assertion_evaluation"] is not None
    ]
    summary = {
        "eligible_sample_count": len(samples),
        "page_count": len(pages),
        "failed_page_count": len(failures),
        "surface_quality": dict(sorted(quality_counts.items())),
        "anchor_count": len(anchor_checks),
        "matched_anchor_count": sum(check["matched"] for check in anchor_checks),
        "unmatched_anchor_ids": [
            check["anchor_id"] for check in anchor_checks if not check["matched"]
        ],
        "table_assertion_count": len(table_evaluations),
        "passed_table_assertion_count": sum(
            item["all_checks_passed"] for item in table_evaluations
        ),
        "elapsed_ms_total": round(sum(page["elapsed_ms"] for page in pages), 3),
        "peak_working_set_mb": max(
            (
                page["resource_usage"]["peak_working_set_mb"]
                for page in pages
            ),
            default=None,
        ),
    }
    config = {
        "config_version": BASELINE_CONFIG_VERSION,
        "dpi": dpi,
        "included_route_hypotheses": sorted(OCR_ROUTES),
        "text_comparison": "remove_whitespace_then_exact_substring",
        "coordinate_origin": "top_left",
        "page_numbering": "physical_page_1_based_and_page_index_0_based",
        "table_recognition": table_recognition,
        "formula_recognition": False,
        "document_orientation": False,
        "document_unwarping": False,
        "textline_orientation": False,
        "mkldnn": False,
        "minimum_line_confidence_for_auto_pass": 0.8,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode("utf-8")
    ).hexdigest()
    resolved_engine = parser.engine
    engine_name = resolved_engine.name if resolved_engine is not None else parser.name
    engine_version = resolved_engine.version if resolved_engine is not None else "3.7.0"
    metadata = {
        "processing_run_id": f"m2-ocr-{engine_version}-{config_hash[:12]}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parser_name": parser.name,
        "ocr_profile": ocr_profile or (
            "table_structure_heavy" if table_recognition else "text_layout_light"
        ),
        "engine_name": engine_name,
        "engine_version": engine_version,
        "python_version": platform.python_version(),
        "input_manifest_id": manifest["manifest_id"],
        "input_manifest_sha256": _sha256(registry / "experiment-sample-v0.json"),
        "anchor_set_sha256": _sha256(registry / "m2-anchor-review-v0.json"),
        "table_assertion_set_sha256": _sha256(table_assertion_path),
        "sample_set_sha256": _sha256(registry / "m2-page-sample-v0.csv"),
        "route_plan_sha256": _sha256(route_plan_path),
        "included_route_hypotheses": sorted(OCR_ROUTES),
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
