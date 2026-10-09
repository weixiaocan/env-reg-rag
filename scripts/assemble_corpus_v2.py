#!/usr/bin/env python
"""Assemble V2 Canonical documents from the page-intermediate OCR cache (plan 2→3).

Reads every row of ``data/canonical/<corpus_version>-documents.jsonl`` (the
page-intermediate OCR cache with complete ``regions`` for tables/figures),
loads each document's per-page formula cache, runs ``assemble_document`` +
``compute_ids`` + ``validate_document``, and writes the V2 JSON artifact to
``data/canonical/v2/<corpus_version>/<sha>.canonical.json``.

This is the same pipeline as ``assemble_corpus_v2.py`` but applied to the whole
corpus. It does NOT invoke any OCR / PDF engine; it only re-organises the
page-intermediate OCR cache (text + table regions + figure regions + formula
cache) into the V2 Element-owned structure.

The corpus version defaults to the one published in ``corpus-current.json``
(via ``resolve_current_corpus``); override with ``--corpus-version`` to
assemble a freshly built candidate before it is published. The formula cache
run-id defaults to the V2 baseline; override with ``--formula-run-id``.

quarantine pages: the V2 assembly already routes quarantine pages to
``parse_status=parsed`` when they still carry text/elements (their content enters
the Canonical). Downstream chunking marks these via element provenance. This
script records per-document quarantine page counts in the summary so quality is
visible.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/assemble_corpus_v2.py
    PYTHONPATH=src python scripts/assemble_corpus_v2.py --only <sha1> <sha2> ...
    PYTHONPATH=src python scripts/assemble_corpus_v2.py --corpus-version corpus-<new> --formula-run-id <run>
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
    apply_computed_ids,
    compute_ids,
    validate_document,
)
from src.application.corpus_artifacts import resolve_current_corpus  # noqa: E402
from src.application.publish_state import (  # noqa: E402
    CandidateConflictError,
    write_artifact_manifest,
    write_canonical_candidate,
    write_quality_report,
)
from src.domain.canonical_document import SchemaError  # noqa: E402
from src.ingestion.text_quality import document_text_quality  # noqa: E402

_DEFAULT_FORMULA_RUN_ID = "5d3264515515e228"
_DEFAULT_INDEPENDENT_OCR_RUN_ID = "ocrv6-ef06411e1606ba35"


def _iter_page_documents(corpus_version: str, root: str | None = None) -> list[dict[str, Any]]:
    page_documents = os.path.join(
        root or _REPO_ROOT, "data", "canonical", f"{corpus_version}-documents.jsonl"
    )
    docs: list[dict[str, Any]] = []
    with open(page_documents, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            docs.append(json.loads(line))
    return docs


def _assert_formula_cache(
    page_doc: dict[str, Any], formula_run_id: str, cache_dir: str
) -> None:
    """Forbid silent 0-formula: fail if the formula cache directory is missing.

    ``assemble_corpus_v2`` reads formula elements ONLY from
    ``corpus_formulas/<run-id>/`` (``canonical_assembly._collect_page_items``
    skips formula in ``page["regions"]``). A missing cache dir means
    ``recognize_corpus_formulas`` was never run for this run-id — assembly would
    silently produce 0 formula elements. Fail loudly instead.
    """
    if not os.path.isdir(cache_dir):
        raise FileNotFoundError(
            f"formula cache directory not found: {cache_dir}\n"
            f"  document={page_doc.get('file_name', '')} sha={page_doc['sha256'][:12]}\n"
            f"  recognize_corpus_formulas was not run for run-id "
            f"{formula_run_id!r}; run build_corpus.py (which orchestrates it) "
            f"or scripts/recognize_corpus_formulas.py first."
        )


def _assert_formula_coverage(
    page_doc: dict[str, Any],
    formula_pages: dict[int, list[dict[str, Any]]],
    formula_run_id: str,
    cache_dir: str,
) -> None:
    """Forbid silent 0-formula: fail if the PDF has formulas but the cache is empty.

    The page-intermediate cache carries formula regions (written by
    ``UnifiedPageExtractor``'s own live recognition), so they are ground truth
    for "this PDF contains formulas". If the page-intermediate has formula
    regions but ``load_formula_pages`` loaded none, the recognize cache is
    incomplete for this document → assembly would drop every formula. Fail.
    """
    page_formula_regions = sum(
        1
        for p in page_doc.get("pages") or []
        for r in p.get("regions") or []
        if r.get("kind") == "formula"
    )
    loaded = sum(len(v) for v in formula_pages.values())
    if page_formula_regions > 0 and loaded == 0:
        raise RuntimeError(
            f"formula cache empty for {page_doc.get('file_name', '')} "
            f"(sha={page_doc['sha256'][:12]}): page-intermediate carries "
            f"{page_formula_regions} formula region(s) but run-id "
            f"{formula_run_id!r} loaded 0 from {cache_dir}. "
            f"Re-run scripts/recognize_corpus_formulas.py for this run-id."
        )


def _assemble_one(
    page_doc: dict[str, Any], formula_run_id: str, independent_ocr_run_id: str,
    *, root: str | None = None,
) -> dict[str, Any]:
    sha = page_doc["sha256"]
    page_count = len(page_doc.get("pages") or [])
    formula_cache_dir = os.path.join(
        root or _REPO_ROOT, "data", "model_runtime", "corpus_formulas", formula_run_id
    )
    _assert_formula_cache(page_doc, formula_run_id, formula_cache_dir)
    formula_pages, cache_failures = load_formula_pages(
        sha, range(1, page_count + 1), cache_dir=formula_cache_dir
    )
    if cache_failures:
        print(
            f"  [formula] {page_doc.get('file_name', '')} sha={sha[:12]}: "
            f"{len(cache_failures)} page(s) without a cache file "
            f"(pages {[f['physical_page'] for f in cache_failures]}) -- "
            f"detected regions there close as explicit failed outcomes"
        )
    _assert_formula_coverage(page_doc, formula_pages, formula_run_id, formula_cache_dir)
    independent_run_dir = os.path.join(
        root or _REPO_ROOT, "data", "model_runtime", "independent_ocr", "runs",
        independent_ocr_run_id,
    )
    doc = assemble_document(
        page_doc, formula_pages=formula_pages,
        independent_ocr_run_dir=independent_run_dir,
    )
    ids = compute_ids(doc)
    apply_computed_ids(doc, ids)
    validate_document(doc)
    return doc


def _verify_upstream_page_intermediate(corpus_version: str, root: str | None = None) -> None:
    """T06: verify the page-intermediate against the build-stage manifest hash.

    Assembly consumes the build output; if the jsonl on disk no longer
    matches the ``canonical_documents_sha256`` the registry manifest bound at
    build time, assembling would silently use unverified input. Hard fail.
    """
    base = root or _REPO_ROOT
    manifest_path = os.path.join(base, "data", "registry", f"{corpus_version}.json")
    jsonl_path = os.path.join(base, "data", "canonical", f"{corpus_version}-documents.jsonl")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"registry manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    expected = manifest.get("canonical_documents_sha256")
    if not expected:
        raise RuntimeError(
            f"registry manifest {manifest_path} does not bind "
            "canonical_documents_sha256; rebuild the page-intermediate first")
    import hashlib as _hashlib
    digest = _hashlib.sha256()
    with open(jsonl_path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise RuntimeError(
            f"page-intermediate hash mismatch for {corpus_version}: the jsonl "
            "on disk does not match the manifest bound at build time "
            f"({digest.hexdigest()[:12]} != {expected[:12]}); refusing to "
            "assemble from unverified upstream input")


def _write(doc: dict[str, Any], corpus_version: str, root: str | None = None) -> str:
    """T06: candidate-only write -- never overwrite an existing artifact."""
    sha = doc["source"]["sha256"]
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    status = write_canonical_candidate(
        root or _REPO_ROOT, corpus_version, f"{sha}.canonical.json", data)
    return os.path.join(root or _REPO_ROOT, "data", "canonical", "v2",
                        corpus_version, f"{sha}.canonical.json")




def _assert_text_quality(page_doc: dict[str, Any], *, allow_garbled: bool) -> dict[str, Any]:
    """P3-b: refuse to assemble documents whose page text is wholesale unreadable."""
    report = document_text_quality(page_doc)
    if report["verdict"] == "garbled_text" and not allow_garbled:
        raise RuntimeError(
            f"garbled_text hard-fail: {report.get('display') or page_doc.get('file_name')} "
            f"sha={page_doc['sha256'][:12]} bad_pages={report['bad_pages']}/"
            f"{report['pages']} han_ratio={report['overall_han_ratio']} "
            f"reasons={report['bad_reasons']}"
        )
    return report

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
    fo = Counter(
        o["execution_status"] for o in doc.get("structured_outcomes") or []
        if o.get("kind") == "formula")
    size_kb = os.path.getsize(out_path) / 1024.0
    print(
        f"[{idx:>2}/{total}] {fn:<42} sha={sha[:12]} "
        f"pages={len(doc.get('pages') or []):<4} elems={len(elements):<5} "
        f"text={tc.get('text',0):<4} table={tc.get('table',0):<3} "
        f"formula={tc.get('formula',0):<3} figure={tc.get('figure',0):<3} "
        f"f_out(c/f/u)={fo.get('completed',0)}/{fo.get('failed',0)}/"
        f"{fo.get('unsupported',0):<3} "
        f"quar={quar:<3} {size_kb:.0f}KB"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--corpus-version", default=None,
        help="corpus version to assemble (defaults to the published corpus-current pointer)",
    )
    parser.add_argument(
        "--formula-run-id", default=_DEFAULT_FORMULA_RUN_ID,
        help=f"formula cache run-id (defaults to {_DEFAULT_FORMULA_RUN_ID!r})",
    )
    parser.add_argument(
        "--independent-ocr-run-id", default=_DEFAULT_INDEPENDENT_OCR_RUN_ID,
        help=("existing local independent OCR run used for calibrated text fusion "
              f"(defaults to {_DEFAULT_INDEPENDENT_OCR_RUN_ID!r}; missing pages hard-fail)"),
    )
    parser.add_argument(
        "--only", nargs="*", default=None,
        help="only assemble these sha256 prefixes (space-separated)",
    )
    parser.add_argument(
        "--allow-garbled", action="store_true",
        help="diagnostic only: assemble even when document verdict is garbled_text",
    )
    parser.add_argument(
        "--root", default=None,
        help="project root override (tests only)",
    )
    args = parser.parse_args(argv)

    root = args.root or _REPO_ROOT
    corpus_version = args.corpus_version or resolve_current_corpus(
        root).corpus_version
    output_dir = os.path.join(root, "data", "canonical", "v2", corpus_version)

    all_docs = _iter_page_documents(corpus_version, root)
    if args.only:
        wanted = {p.lower() for p in args.only}
        all_docs = [d for d in all_docs if d["sha256"].lower().startswith(tuple(wanted))]
        if not all_docs:
            print(f"[assemble] no documents matched --only {args.only}")
            return 1

    total = len(all_docs)
    print(f"[assemble] {total} documents to assemble from {corpus_version}")
    print(f"[assemble] formula run-id: {args.formula_run_id}")
    print(f"[assemble] independent OCR run-id: {args.independent_ocr_run_id}")
    print(f"[assemble] output dir: {output_dir}")
    print()

    # T06: verify the build-stage page-intermediate hash BEFORE consuming it.
    _verify_upstream_page_intermediate(corpus_version, root)

    ok = 0
    failed: list[tuple[str, str]] = []
    quality_rows: list[dict[str, Any]] = []
    for idx, page_doc in enumerate(all_docs, 1):
        sha = page_doc["sha256"]
        fn = page_doc.get("file_name") or ""
        try:
            quality = _assert_text_quality(page_doc, allow_garbled=args.allow_garbled)
            quality_rows.append({
                "sha256": sha,
                "verdict": quality.get("verdict"),
                "bad_pages": quality.get("bad_pages"),
                "pages": quality.get("pages"),
            })
            doc = _assemble_one(
                page_doc, args.formula_run_id, args.independent_ocr_run_id, root=root)
            out_path = _write(doc, corpus_version, root)
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

    # T06: bind the candidate with a quality report + artifact manifest
    # (full-corpus runs only -- a partial --only run must not mark quality).
    if not args.only:
        reasons = ["allow_garbled"] if args.allow_garbled else None
        write_quality_report(root, corpus_version, quality_rows,
                             non_publishable_reasons=reasons)
        write_artifact_manifest(
            root, corpus_version, non_publishable_reasons=reasons)
        if args.allow_garbled:
            print("[assemble] WARNING: --allow-garbled diagnostic artifact -- "
                  "marked NON-PUBLISHABLE; ingest/publish will refuse it")
        print("[assemble] artifact manifest written "
              "(page-intermediate + canonicals + quality report bound)")

    # Aggregate summary across the whole corpus.
    _print_corpus_summary(output_dir)
    return 0


def _print_corpus_summary(output_dir: str) -> None:
    """Re-scan the output dir and print aggregate element-type counts."""
    totals: Counter[str] = Counter()
    doc_count = 0
    for name in sorted(os.listdir(output_dir)):
        if not name.endswith(".canonical.json"):
            continue
        with open(os.path.join(output_dir, name), "r", encoding="utf-8") as fh:
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
