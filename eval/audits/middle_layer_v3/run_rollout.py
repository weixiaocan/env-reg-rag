#!/usr/bin/env python
"""Build the R2 23-document v3 rollout in memory and emit audit artifacts."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.assemble_corpus_v2 import _assemble_one, _iter_page_documents  # noqa: E402
from src.application.v2_chunker import chunk_document  # noqa: E402
from src.ingestion.block_assembly import prepare_page_document_v3  # noqa: E402
from eval.audits.middle_layer_v3.run_pilot_gb55027 import (  # noqa: E402
    _chunk_row,
    _metrics,
)


CORPUS_VERSION = "corpus-0ae8a9423e2f"
FORMULA_RUN = "5d3264515515e228"
CLEAN_OCR_RUN = "ocrv6-fe621f3631762fd6"
RAW_OCR_RUN = "ocrv6-ef06411e1606ba35"
SAMPLE_SEED = "middle-layer-v3-r2-review-samples-v1"
OUT = ROOT / "eval" / "audits" / "middle_layer_v3" / "rollout"


def main() -> int:
    documents = _iter_page_documents(CORPUS_VERSION)
    clean_dir = _ocr_dir(CLEAN_OCR_RUN)
    raw_dir = _ocr_dir(RAW_OCR_RUN)
    clean_manifest = _read_json(clean_dir / "run_manifest.json")
    raw_manifest = _read_json(raw_dir / "run_manifest.json")
    registry_manifest = _read_json(ROOT / "data" / "registry" / f"{CORPUS_VERSION}.json")
    source_paths = {
        str(asset["sha256"]): ROOT / str(asset["canonical_rel_path"])
        for asset in registry_manifest.get("assets") or []
    }

    OUT.mkdir(parents=True, exist_ok=True)
    review_dir = OUT / "review_samples"
    review_dir.mkdir(parents=True, exist_ok=True)
    image_dir = review_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    render_failures: list[dict[str, Any]] = []

    for index, page_doc in enumerate(documents, start=1):
        sha = str(page_doc["sha256"])
        name = str(page_doc.get("file_name") or sha)
        print(f"[{index:02d}/{len(documents)}] {name}", flush=True)
        try:
            prepared = prepare_page_document_v3(
                page_doc,
                independent_ocr_run_dir=str(clean_dir),
                raw_independent_ocr_run_dir=str(raw_dir),
            )
            v3 = _assemble_one(
                page_doc, FORMULA_RUN, CLEAN_OCR_RUN,
                raw_independent_ocr_run_id=RAW_OCR_RUN, middle_layer="v3",
            )
            v3_chunks = chunk_document(v3)
            document_rows = [_chunk_row(v3, chunk) for chunk in v3_chunks]
            rows.extend(document_rows)
        except Exception as exc:  # keep a complete, explicit failure ledger
            failures.append({"sha256": sha, "document": name,
                             "stage": "v3", "error": f"{type(exc).__name__}: {exc}"})
            print(f"  FAILED v3: {type(exc).__name__}: {exc}", flush=True)
            continue

        legacy_metrics: dict[str, Any] | None = None
        try:
            legacy = _assemble_one(
                page_doc, FORMULA_RUN, RAW_OCR_RUN, middle_layer="legacy")
            legacy_chunks = chunk_document(legacy)
            legacy_metrics = _metrics(legacy, legacy_chunks)
        except Exception as exc:
            failures.append({"sha256": sha, "document": name,
                             "stage": "legacy_baseline",
                             "error": f"{type(exc).__name__}: {exc}"})
            print(f"  FAILED legacy: {type(exc).__name__}: {exc}", flush=True)

        v3_metrics = _metrics(v3, v3_chunks)
        metrics_rows.append({
            "sha256": sha,
            "document": name,
            "physical_page_count": len(page_doc.get("pages") or []),
            "route_counts": dict(Counter(
                str(page.get("extraction_route") or "unknown")
                for page in page_doc.get("pages") or []
            )),
            "canonical_validation": "passed",
            "v3_preparation": prepared.get("_v3_stats"),
            "three_source_vote": prepared.get("_v3_ocr_summary") or {
                "mode": "not_applicable_native_only", "ocr_page_count": 0,
            },
            "v3": v3_metrics,
            "legacy": legacy_metrics,
        })
        sample_result = _write_review_sample(
            page_doc, document_rows, source_paths.get(sha), review_dir, image_dir)
        render_failures.extend(sample_result["render_failures"])

    chunks_path = OUT / "chunks.jsonl"
    chunks_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    payload = {
        "schema_version": "middle-layer-v3-rollout-metrics/1",
        "corpus_version": CORPUS_VERSION,
        "formula_run_id": FORMULA_RUN,
        "clean_ocr_run": _ocr_manifest_summary(clean_manifest, clean_dir),
        "raw_ocr_run": _ocr_manifest_summary(raw_manifest, raw_dir),
        "document_count": len(documents),
        "canonical_validation": {
            "passed": len(metrics_rows), "total": len(documents),
            "failed": len([item for item in failures if item["stage"] == "v3"]),
        },
        "summary": {
            "v3": _aggregate(metrics_rows, "v3"),
            "legacy": _aggregate(metrics_rows, "legacy"),
        },
        "documents": metrics_rows,
        "failures": failures,
        "review_sample_render_failures": render_failures,
    }
    (OUT / "metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "documents": payload["canonical_validation"],
        "chunks": len(rows),
        "v3": _compact_summary(payload["summary"]["v3"]),
        "legacy": _compact_summary(payload["summary"]["legacy"]),
        "render_failures": len(render_failures),
    }, ensure_ascii=False, indent=2))
    return 1 if failures or render_failures else 0


def _aggregate(documents: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [row[key] for row in documents if row.get(key) is not None]
    additive = (
        "element_count", "chunk_count", "text_body_chunk_count",
        "body_page_number_footer_watermark_lines",
        "sentence_ended_heading_count", "body_breadcrumb_count",
        "section_title_over_60_count", "list_item_root_count",
        "short_body_chunk_lt15_count", "ocr_text_source_element_count",
        "ocr_text_source_missing_run_id_count",
    )
    result = {name: sum(int(value.get(name) or 0) for value in values) for name in additive}
    chunks = result["text_body_chunk_count"]
    result["short_body_chunk_lt15_ratio"] = round(
        result["short_body_chunk_lt15_count"] / chunks, 6) if chunks else 0.0
    result["element_type_counts"] = dict(sum(
        (Counter(value.get("element_type_counts") or {}) for value in values), Counter()))
    result["ocr_text_source_counts"] = dict(sum(
        (Counter(value.get("ocr_text_source_counts") or {}) for value in values), Counter()))
    continuity = {
        "missing": [], "duplicates": [], "illegal": [], "illegal_jumps": [],
        "summary": Counter(),
    }
    for row in documents:
        value = row.get(key)
        if not value:
            continue
        checks = value.get("clause_number_continuity") or {}
        for issue_key in ("missing", "duplicates", "illegal", "illegal_jumps"):
            continuity[issue_key].extend(
                {"document": row["document"], "sha256": row["sha256"], **issue}
                if isinstance(issue, dict)
                else {"document": row["document"], "sha256": row["sha256"],
                      "issue": issue}
                for issue in checks.get(issue_key) or []
            )
        continuity["summary"].update(checks.get("summary") or {})
    continuity["summary"] = dict(continuity["summary"])
    result["clause_number_continuity"] = continuity
    return result


def _compact_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in metrics.items()
        if key not in {"clause_number_continuity"}
    } | {
        "clause_number_continuity":
            (metrics.get("clause_number_continuity") or {}).get("summary")
    }


def _write_review_sample(
    page_doc: dict[str, Any], rows: list[dict[str, Any]], source_pdf: Path | None,
    review_dir: Path, image_dir: Path,
) -> dict[str, Any]:
    sha = str(page_doc["sha256"])
    candidates = [row for row in rows if "text" in row["element_types"] and row["body"].strip()]
    rng = random.Random(f"{SAMPLE_SEED}:{sha}")
    selected = rng.sample(candidates, min(5, len(candidates)))
    render_failures: list[dict[str, Any]] = []
    rendered: dict[int, str] = {}
    for row in selected:
        for page in row["pages"]:
            if page in rendered:
                continue
            relative = Path("eval/audits/middle_layer_v3/rollout/review_samples/images") / f"{sha[:12]}-p{page:04d}.png"
            target = ROOT / relative
            try:
                _render_page(source_pdf, page, target)
                rendered[page] = relative.as_posix()
            except Exception as exc:
                render_failures.append({
                    "sha256": sha, "document": page_doc.get("file_name"),
                    "physical_page": page, "error": f"{type(exc).__name__}: {exc}",
                })
        row["rendered_page_paths"] = [rendered[p] for p in row["pages"] if p in rendered]
    payload = {
        "schema_version": "middle-layer-v3-review-samples/1",
        "seed": SAMPLE_SEED,
        "sha256": sha,
        "document": page_doc.get("file_name"),
        "source_pdf": source_pdf.relative_to(ROOT).as_posix() if source_pdf else None,
        "requested_sample_count": 5,
        "sample_count": len(selected),
        "samples": selected,
    }
    (review_dir / f"{sha[:12]}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"render_failures": render_failures}


def _render_page(source_pdf: Path | None, physical_page: int, target: Path) -> None:
    if source_pdf is None or not source_pdf.is_file():
        raise FileNotFoundError(f"source PDF unavailable: {source_pdf}")
    import fitz
    target.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(source_pdf) as pdf:
        if not 1 <= physical_page <= len(pdf):
            raise IndexError(f"physical page {physical_page} outside 1..{len(pdf)}")
        pixmap = pdf[physical_page - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        pixmap.save(target)


def _ocr_dir(run_id: str) -> Path:
    return ROOT / "data" / "model_runtime" / "independent_ocr" / "runs" / run_id


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ocr_manifest_summary(manifest: dict[str, Any], directory: Path) -> dict[str, Any]:
    selected = int(manifest.get("selected_page_count") or 0)
    completed = int(manifest.get("completed_page_count") or 0)
    return {
        "run_id": manifest.get("run_id"),
        "run_manifest": directory.relative_to(ROOT).as_posix() + "/run_manifest.json",
        "run_manifest_sha256": hashlib.sha256(
            (directory / "run_manifest.json").read_bytes()).hexdigest(),
        "selected_page_count": selected,
        "completed_page_count": completed,
        "failed_page_count": max(0, selected - completed),
        "config": manifest.get("config"),
        "input_hashes": manifest.get("input_hashes"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
