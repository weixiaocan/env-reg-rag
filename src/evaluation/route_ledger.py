"""Build the fixed M2 page-routing ledger from versioned experiment evidence."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ratio(evaluation: dict[str, Any] | None, count_key: str, pass_key: str) -> str:
    if not evaluation:
        return "not_applicable"
    return f"{evaluation.get(pass_key, 0)}/{evaluation.get(count_key, 0)}"


def build_route_ledger(
    project_root: Path,
    output_dir: Path,
    *,
    artifact_prefix: str = "m2-page-route-ledger-v1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Merge fixed M2 evidence into one explainable decision per sample page."""

    root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{artifact_prefix}.csv"
    metadata_path = output_dir / f"{artifact_prefix}.meta.json"
    if (csv_path.exists() or metadata_path.exists()) and not overwrite:
        raise FileExistsError("route ledger output exists; use overwrite or a new prefix")

    registry = root / "data" / "registry"
    results = root / "data" / "eval_results"
    input_paths = {
        "sample_set": registry / "m2-page-sample-v0.csv",
        "ocr_route_plan": registry / "m2-ocr-route-plan-v1.csv",
        "failure_attribution": registry / "m2-ocr-failure-attribution-v1.json",
        "route_overrides": registry / "m2-route-overrides-v1.json",
        "native_pages": results / "m2-native-pymupdf-1.28.2-v1-pages.jsonl",
        "ocr_light_pages": results / "m2-ocr-routed-light-v1-pages.jsonl",
        "ocr_heavy_pages": results / "m2-ocr-routed-heavy-v1-pages.jsonl",
        "docling_pages": results / "m2-docling-2.126.0-v1-pages.jsonl",
    }
    input_hashes = {name: _sha256(path) for name, path in input_paths.items()}

    samples = _read_csv(input_paths["sample_set"])
    native_by_sample = {
        page["sample_id"]: page for page in _read_jsonl(input_paths["native_pages"])
    }
    ocr_pages = [
        *_read_jsonl(input_paths["ocr_light_pages"]),
        *_read_jsonl(input_paths["ocr_heavy_pages"]),
    ]
    ocr_by_sample = {page["sample_id"]: page for page in ocr_pages}
    route_plan = {
        row["sample_id"]: row for row in _read_csv(input_paths["ocr_route_plan"])
    }
    attribution = json.loads(input_paths["failure_attribution"].read_text(encoding="utf-8"))
    attribution_by_sample: dict[str, list[dict[str, Any]]] = {}
    for item in attribution["items"]:
        attribution_by_sample.setdefault(item["sample_id"], []).append(item)
    overrides = json.loads(input_paths["route_overrides"].read_text(encoding="utf-8"))
    override_by_sample = {item["sample_id"]: item for item in overrides["overrides"]}

    rows = []
    for sample in samples:
        sample_id = sample["sample_id"]
        hypothesis = sample["route_hypothesis"]
        reasons: list[str] = []
        quality_status = "not_applicable"
        anchor_result = "not_applicable"
        table_result = "not_applicable"

        if hypothesis == "blank_skip_candidate":
            selected_route = "skip_blank"
            selected_parser = "none"
            selected_profile = "visual_blank"
            selected_artifact = "data/registry/m2-page-sample-v0.csv"
            decision_status = "skip_blank"
            reasons.append("human_selected_visual_blank")
        elif hypothesis == "native_candidate":
            page = native_by_sample[sample_id]
            selected_route = "native"
            selected_parser = "pymupdf"
            selected_profile = "native_text_and_blocks"
            selected_artifact = "data/eval_results/m2-native-pymupdf-1.28.2-v1-pages.jsonl"
            quality_status = page["quality"]["status"]
            anchor = page["anchor_evaluation"]
            anchor_result = _ratio(anchor, "anchor_count", "matched_count")
            if quality_status == "pass" and anchor.get("all_matched") is not False:
                decision_status = "approved"
                reasons.append("native_quality_gate_passed")
            else:
                decision_status = "needs_manual_review"
                reasons.append("native_anchor_or_quality_gate_requires_review")
        else:
            page = ocr_by_sample[sample_id]
            plan = route_plan[sample_id]
            selected_route = (
                "hybrid_ocr" if hypothesis == "hybrid_candidate" else "full_ocr"
            )
            selected_parser = "paddleocr-ppstructurev3"
            selected_profile = plan["ocr_profile"]
            selected_artifact = (
                "data/eval_results/m2-ocr-routed-light-v1-pages.jsonl"
                if plan["ocr_profile"] == "text_layout_light"
                else "data/eval_results/m2-ocr-routed-heavy-v1-pages.jsonl"
            )
            quality_status = page["quality"]["status"]
            anchor = page["anchor_evaluation"]
            table = page.get("table_assertion_evaluation")
            anchor_result = _ratio(anchor, "anchor_count", "matched_count")
            table_result = _ratio(table, "check_count", "passed_check_count")

            attributed = attribution_by_sample.get(sample_id, [])
            false_negative_ids = {
                item["anchor_id"]
                for item in attributed
                if item["classification"] == "evaluation_false_negative"
            }
            content_error_ids = {
                item["anchor_id"]
                for item in attributed
                if item["classification"] == "ocr_content_error"
            }
            unmatched_ids = {
                check["anchor_id"] for check in anchor["checks"] if not check["matched"]
            }
            if table and table["all_checks_passed"]:
                unmatched_ids = {
                    anchor_id for anchor_id in unmatched_ids if not anchor_id.startswith("TAB-")
                }
            effective_unmatched = unmatched_ids - false_negative_ids
            mandatory_reason = plan["mandatory_review_reason"]

            if mandatory_reason == "formula_structure":
                decision_status = "quarantine"
                reasons.append("formula_structure_not_reliably_recovered")
            elif (
                quality_status != "pass"
                or mandatory_reason
                or effective_unmatched
                or (table is not None and not table["all_checks_passed"])
                or content_error_ids
            ):
                decision_status = "needs_manual_review"
                if quality_status != "pass":
                    reasons.append("ocr_low_confidence_gate")
                if mandatory_reason:
                    reasons.append(f"mandatory_{mandatory_reason}_review")
                if effective_unmatched or content_error_ids:
                    reasons.append("approved_anchor_content_error_or_unresolved_miss")
                if table is not None and not table["all_checks_passed"]:
                    reasons.append("table_assertion_failed")
            else:
                decision_status = "approved"
                reasons.append("ocr_quality_and_evidence_gates_passed")
                if false_negative_ids:
                    reasons.append("known_exact_match_false_negative_waived")

        override = override_by_sample.get(sample_id)
        if override:
            selected_route = override["selected_route"]
            selected_parser = override["selected_parser"]
            selected_profile = override["selected_profile"]
            selected_artifact = override["selected_artifact"]
            decision_status = override["decision_status"]
            reasons = list(override["decision_reasons"])

        rows.append(
            {
                "sample_id": sample_id,
                "manifest_order": sample["manifest_order"],
                "file_name": sample["file_name"],
                "pdf_page": sample["pdf_page"],
                "content_type": sample["content_type"],
                "route_hypothesis": hypothesis,
                "selected_route": selected_route,
                "selected_parser": selected_parser,
                "selected_profile": selected_profile,
                "decision_status": decision_status,
                "quality_status": quality_status,
                "anchor_result": anchor_result,
                "table_assertion_result": table_result,
                "decision_reasons": "|".join(reasons),
                "selected_artifact": selected_artifact,
            }
        )

    if len(rows) != 40 or len({row["sample_id"] for row in rows}) != 40:
        raise ValueError("fixed M2 ledger must contain exactly 40 unique samples")
    status_counts = Counter(row["decision_status"] for row in rows)
    route_counts = Counter(row["selected_route"] for row in rows)
    summary = {
        "sample_count": len(rows),
        "decision_status_counts": dict(sorted(status_counts.items())),
        "selected_route_counts": dict(sorted(route_counts.items())),
    }

    fieldnames = list(rows[0])
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    metadata = {
        "artifact_id": artifact_prefix,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_hashes,
        "summary": summary,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"rows": rows, "summary": summary, "metadata": metadata}
