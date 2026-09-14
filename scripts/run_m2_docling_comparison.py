"""Run the fixed M2 Docling structural comparison on selected difficult pages."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    RapidOcrOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.table_assertions import evaluate_table_assertion

DEFAULT_SAMPLE_IDS = {"M2-P015", "M2-P024", "M2-P026", "M2-P036"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def markdown_table_texts(markdown: str) -> list[str]:
    tables = []
    current = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("|"):
            current.append(line)
        elif current:
            tables.append("\n".join(current))
            current = []
    if current:
        tables.append("\n".join(current))
    return tables


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    parser.add_argument("--artifact-prefix", default="m2-docling-2.126.0-v1")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output_dir = ROOT / "data" / "eval_results"
    pages_path = output_dir / f"{args.artifact_prefix}-pages.jsonl"
    metadata_path = output_dir / f"{args.artifact_prefix}-run-metadata.json"
    summary_path = output_dir / f"{args.artifact_prefix}-summary.json"
    if any(path.exists() for path in (pages_path, metadata_path, summary_path)) and not args.overwrite:
        raise FileExistsError("Docling comparison output exists; use a new prefix or --overwrite")

    registry = ROOT / "data" / "registry"
    selected_ids = set(args.sample_ids or DEFAULT_SAMPLE_IDS)
    sample_rows = read_csv(registry / "m2-page-sample-v0.csv")
    known_ids = {row["sample_id"] for row in sample_rows}
    if selected_ids - known_ids:
        raise ValueError(f"unknown sample ids: {sorted(selected_ids - known_ids)}")
    samples = [row for row in sample_rows if row["sample_id"] in selected_ids]
    inventory = {row["file_name"]: row for row in read_csv(registry / "inventory.csv")}
    anchor_set = json.loads(
        (registry / "m2-anchor-review-v0.json").read_text(encoding="utf-8-sig")
    )
    anchors_by_sample: dict[str, list[dict]] = {}
    for anchor in anchor_set["anchors"]:
        anchors_by_sample.setdefault(anchor["sample_id"], []).append(anchor)
    table_assertion_path = registry / "m2-table-anchor-assertions-v1.json"
    table_assertion_set = json.loads(table_assertion_path.read_text(encoding="utf-8-sig"))
    table_assertion_by_sample = {
        item["sample_id"]: item for item in table_assertion_set["anchors"]
    }

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = RapidOcrOptions(lang=["chinese"], backend="torch")
    options.ocr_options.force_full_page_ocr = True
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=options,
                backend=PyPdfiumDocumentBackend,
            )
        }
    )

    pages = []
    failures = []
    verified_documents = {}
    for completed, sample in enumerate(samples, start=1):
        inventory_row = inventory[sample["file_name"]]
        source = ROOT / inventory_row["rel_path"]
        actual_hash = sha256(source)
        if actual_hash != inventory_row["sha256"].lower():
            raise ValueError(f"local PDF SHA-256 changed: {sample['file_name']}")
        verified_documents[sample["file_name"]] = actual_hash
        started = time.perf_counter()
        try:
            page_number = int(sample["pdf_page"])
            result = converter.convert(source, page_range=(page_number, page_number))
            text = result.document.export_to_text()
            markdown = result.document.export_to_markdown()
            table_texts = markdown_table_texts(markdown)
            normalized_text = re.sub(r"\s+", "", text)
            anchor_checks = [
                {
                    "anchor_id": anchor["anchor_id"],
                    "matched": re.sub(r"\s+", "", anchor["proposed_truth"])
                    in normalized_text,
                }
                for anchor in anchors_by_sample.get(sample["sample_id"], [])
            ]
            memory = psutil.Process().memory_info()
            page = {
                    "sample_id": sample["sample_id"],
                    "file_name": sample["file_name"],
                    "document_sha256": actual_hash,
                    "physical_page": page_number,
                    "content_type": sample["content_type"],
                    "status": str(result.status),
                    "text": text,
                    "markdown": markdown,
                    "tables": [{"text": item} for item in table_texts],
                    "docling_document": result.document.export_to_dict(),
                    "anchor_evaluation": {
                        "anchor_count": len(anchor_checks),
                        "matched_count": sum(check["matched"] for check in anchor_checks),
                        "checks": anchor_checks,
                    },
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "resource_usage": {
                        "rss_mb": round(memory.rss / (1024 * 1024), 3),
                        "peak_working_set_mb": round(
                            getattr(memory, "peak_wset", memory.rss) / (1024 * 1024), 3
                        ),
                    },
                }
            table_assertion = table_assertion_by_sample.get(sample["sample_id"])
            page["table_assertion_evaluation"] = (
                evaluate_table_assertion(page, table_assertion)
                if table_assertion is not None
                else None
            )
            pages.append(page)
            outcome = "page"
        except Exception as exc:
            failures.append(
                {
                    "sample_id": sample["sample_id"],
                    "file_name": sample["file_name"],
                    "physical_page": int(sample["pdf_page"]),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            outcome = "failure"
        print(f"[DOCLING] {completed}/{len(samples)} {sample['sample_id']} {outcome}", flush=True)

    with pages_path.open("w", encoding="utf-8", newline="\n") as handle:
        for page in pages:
            handle.write(json.dumps(page, ensure_ascii=False) + "\n")
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "docling_version": importlib.metadata.version("docling"),
        "docling_core_version": importlib.metadata.version("docling-core"),
        "docling_parse_version": importlib.metadata.version("docling-parse"),
        "backend": "PyPdfiumDocumentBackend",
        "backend_reason": "docling-parse 7.17.0 cannot open package resources from this Unicode project path on Windows",
        "ocr": "RapidOCR PP-OCRv6 torch chinese force_full_page_ocr",
        "table_structure": "TableFormer accurate",
        "sample_set_sha256": sha256(registry / "m2-page-sample-v0.csv"),
        "anchor_set_sha256": sha256(registry / "m2-anchor-review-v0.json"),
        "table_assertion_set_sha256": sha256(table_assertion_path),
        "sample_ids": [row["sample_id"] for row in samples],
        "documents": [
            {"file_name": name, "sha256": digest}
            for name, digest in verified_documents.items()
        ],
    }
    summary = {
        "eligible_sample_count": len(samples),
        "page_count": len(pages),
        "failed_page_count": len(failures),
        "anchor_count": sum(page["anchor_evaluation"]["anchor_count"] for page in pages),
        "matched_anchor_count": sum(page["anchor_evaluation"]["matched_count"] for page in pages),
        "table_assertion_count": sum(
            page["table_assertion_evaluation"] is not None for page in pages
        ),
        "passed_table_assertion_count": sum(
            bool(page["table_assertion_evaluation"]["all_checks_passed"])
            for page in pages
            if page["table_assertion_evaluation"] is not None
        ),
        "elapsed_ms_total": round(sum(page["elapsed_ms"] for page in pages), 3),
        "peak_working_set_mb": max(
            (page["resource_usage"]["peak_working_set_mb"] for page in pages),
            default=None,
        ),
        "failures": failures,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
