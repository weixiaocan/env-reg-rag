#!/usr/bin/env python
"""Batch: assemble ALL 21 documents in the corpus into V2 Canonical (plan 2→3).

Reads every row of ``data/canonical/corpus-37456321a968-documents.jsonl`` (the
page-intermediate OCR cache with complete ``regions`` for tables/figures —
``corpus-919`` is NOT used because its ``regions`` were stripped), loads each
document's per-page formula cache, runs ``assemble_document`` + ``compute_ids``
+ ``validate_document``, and writes the V2 JSON artifact to
``data/canonical/v2/corpus-37456321a968/<sha>.canonical.json``.

This is the same pipeline as ``assemble_cecs758_v2.py`` but applied to the whole
corpus. It does NOT invoke any OCR / PDF engine; it only re-organises the
page-intermediate OCR cache (text + table regions + figure regions + formula
cache) into the V2 Element-owned structure.

quarantine pages: the V2 assembly already routes quarantine pages to
``parse_status=parsed`` when they still carry text/elements (their content enters
the Canonical). Downstream chunking marks these via element provenance. This
script records per-document quarantine page counts in the summary so quality is
visible.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/assemble_corpus_v2.py
    PYTHONPATH=src python scripts/assemble_corpus_v2.py --only <sha1> <sha2> ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.canonical_assembly import (  # noqa: E402
    assemble_document,
    load_formula_pages,
)
from src.application.canonical_validation import (  # noqa: E402
    compute_ids,
    validate_document,
)
from src.domain.canonical_document import SchemaError  # noqa: E402

CORPUS_VERSION = "corpus-37456321a968"
PAGE_DOCUMENTS = os.path.join(
    _REPO_ROOT, "data", "canonical", f"{CORPUS_VERSION}-documents.jsonl"
)
FORMULA_CACHE_DIR = os.path.join(
    _REPO_ROOT, "data", "model_runtime", "corpus_formulas", "5d3264515515e228"
)
OUTPUT_DIR = os.path.join(_REPO_ROOT, "data", "canonical", "v2", CORPUS_VERSION)


def _iter_page_documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    with open(PAGE_DOCUMENTS, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            docs.append(json.loads(line))
    return docs


def _assemble_one(page_doc: dict[str, Any]) -> dict[str, Any]:
    sha = page_doc["sha256"]
    page_count = len(page_doc.get("pages") or [])
    formula_pages = load_formula_pages(
        sha, range(1, page_count + 1), cache_dir=FORMULA_CACHE_DIR
    )
    doc = assemble_document(page_doc, formula_pages=formula_pages)
    ids = compute_ids(doc)
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]
    validate_document(doc)
    return doc


def _write(doc: dict[str, Any]) -> str:
    sha = doc["source"]["sha256"]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{sha}.canonical.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    return out_path


def _quarantine_count(page_doc: dict[str, Any]) -> int:
    return sum(
        1 for p in page_doc.get("pages") or [] if p.get("decision_status") == "quarantine"
    )


def _print_row(idx: int, total: int, page_doc: dict[str, Any], doc: dict[str, Any],
               out_path: str, quar: int) -> None:
    sha = page_doc["sha256"]
    fn = (page_doc.get("file_name") or "")[:42]
    elements = doc.get("elements") or []
    tc = Counter(e["type"] for e in elements)
    size_kb = os.path.getsize(out_path) / 1024.0
    print(
        f"[{idx:>2}/{total}] {fn:<42} sha={sha[:12]} "
        f"pages={len(doc.get('pages') or []):<4} elems={len(elements):<5} "
        f"text={tc.get('text',0):<4} table={tc.get('table',0):<3} "
        f"formula={tc.get('formula',0):<3} figure={tc.get('figure',0):<3} "
        f"quar={quar:<3} {size_kb:.0f}KB"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only", nargs="*", default=None,
        help="only assemble these sha256 prefixes (space-separated)",
    )
    args = parser.parse_args(argv)

    all_docs = _iter_page_documents()
    if args.only:
        wanted = {p.lower() for p in args.only}
        all_docs = [d for d in all_docs if d["sha256"].lower().startswith(tuple(wanted))]
        if not all_docs:
            print(f"[assemble] no documents matched --only {args.only}")
            return 1

    total = len(all_docs)
    print(f"[assemble] {total} documents to assemble from {CORPUS_VERSION}")
    print(f"[assemble] output dir: {OUTPUT_DIR}")
    print()

    ok = 0
    failed: list[tuple[str, str]] = []
    for idx, page_doc in enumerate(all_docs, 1):
        sha = page_doc["sha256"]
        fn = page_doc.get("file_name") or ""
        try:
            doc = _assemble_one(page_doc)
            out_path = _write(doc)
            quar = _quarantine_count(page_doc)
            _print_row(idx, total, page_doc, doc, out_path, quar)
            ok += 1
        except (SchemaError, Exception) as exc:
            failed.append((sha, f"{type(exc).__name__}: {exc}"))
            print(f"[{idx:>2}/{total}] FAILED {fn[:42]} sha={sha[:12]} -> {exc}")

    print()
    print(f"[assemble] done: {ok}/{total} OK, {len(failed)} failed")
    if failed:
        print("[assemble] failures:")
        for sha, msg in failed:
            print(f"  {sha[:12]}: {msg}")
        return 1

    # Aggregate summary across the whole corpus.
    _print_corpus_summary()
    return 0


def _print_corpus_summary() -> None:
    """Re-scan the output dir and print aggregate element-type counts."""
    totals: Counter[str] = Counter()
    doc_count = 0
    for name in sorted(os.listdir(OUTPUT_DIR)):
        if not name.endswith(".canonical.json"):
            continue
        with open(os.path.join(OUTPUT_DIR, name), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc_count += 1
        for e in doc.get("elements") or []:
            totals[e["type"]] += 1
    print()
    print("=== Corpus V2 aggregate ===")
    print(f"  documents          : {doc_count}")
    print(f"  elements by type   : {dict(totals)}")
    print(f"  total elements     : {sum(totals.values())}")


if __name__ == "__main__":
    raise SystemExit(main())
