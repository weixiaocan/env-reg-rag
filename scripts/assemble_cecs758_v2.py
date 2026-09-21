#!/usr/bin/env python
"""Entrypoint: assemble the CECS758 V2 Canonical Document (plan 1.5).

Reads the page-intermediate ``corpus-37456321a968-documents.jsonl`` row for
CECS758, loads the per-page formula cache, runs :func:`assemble_document` +
:func:`compute_ids` + :func:`validate_document`, and writes the V2 JSON artifact
to ``data/canonical/v2/corpus-37456321a968/<sha>.canonical.json``.

Does NOT invoke any OCR / PDF engine; it only reads the page-intermediate OCR
cache produced by the PDF processing pipeline.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/assemble_cecs758_v2.py
"""
from __future__ import annotations

import json
import os
import sys

# Ensure ``src`` is importable when run as a plain script. The ``src`` package
# lives at ``<repo>/src`` so the repo root must be on sys.path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.canonical_assembly import (  # noqa: E402
    CECS758_SHA,
    assemble_document,
    load_formula_pages,
)
from src.application.canonical_validation import (  # noqa: E402
    compute_ids,
    validate_document,
)

CORPUS_VERSION = "corpus-37456321a968"
PAGE_DOCUMENTS = os.path.join(
    _REPO_ROOT, "data", "canonical", f"{CORPUS_VERSION}-documents.jsonl"
)
FORMULA_CACHE_DIR = os.path.join(
    _REPO_ROOT, "data", "model_runtime", "corpus_formulas", "5d3264515515e228"
)
OUTPUT_DIR = os.path.join(
    _REPO_ROOT, "data", "canonical", "v2", CORPUS_VERSION
)


def _stream_find_page_document(sha: str) -> dict:
    with open(PAGE_DOCUMENTS, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("sha256") == sha:
                return obj
    raise FileNotFoundError(f"CECS758 row not found in {PAGE_DOCUMENTS}")


def main() -> int:
    print(f"[assemble] loading page-intermediate document {CECS758_SHA[:16]}...")
    page_doc = _stream_find_page_document(CECS758_SHA)
    page_count = len(page_doc.get("pages") or [])
    print(f"[assemble] pages: {page_count}")

    print("[assemble] loading per-page formula cache...")
    formula_pages = load_formula_pages(
        CECS758_SHA, range(1, page_count + 1), cache_dir=FORMULA_CACHE_DIR
    )
    total_formula_regions = sum(len(v) for v in formula_pages.values())
    print(
        f"[assemble] formula cache: {len(formula_pages)} pages, "
        f"{total_formula_regions} raw regions"
    )

    print("[assemble] assembling V2 document...")
    doc = assemble_document(page_doc, formula_pages=formula_pages)

    print("[assemble] computing canonical IDs...")
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]

    print("[assemble] validating...")
    validate_document(doc)
    print("[assemble] validation OK")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{CECS758_SHA}.canonical.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    size_kb = os.path.getsize(out_path) / 1024.0
    print(f"[assemble] wrote {out_path} ({size_kb:.1f} KB)")

    _print_summary(doc)
    return 0


def _print_summary(doc: dict) -> None:
    elements = doc.get("elements") or []
    pages = doc.get("pages") or []
    type_counts: dict[str, int] = {}
    for e in elements:
        type_counts[e["type"]] = type_counts.get(e["type"], 0) + 1
    print("\n=== V2 Canonical Document summary ===")
    print(f"  schema_version      : {doc['schema_version']}")
    print(f"  pages               : {len(pages)}")
    print(f"  elements            : {len(elements)}")
    print(f"  element type counts : {type_counts}")
    print(f"  canonical_id        : {doc['canonical_id'][:48]}...")
    print(f"  canonical_content_id: {doc['canonical_content_id'][:48]}...")
    print(f"  metadata_fingerprint: {doc['metadata_fingerprint'][:48]}...")
    print(f"  document_id         : {doc['document_id']}")


if __name__ == "__main__":
    raise SystemExit(main())
