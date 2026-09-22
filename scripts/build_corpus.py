#!/usr/bin/env python
"""Build the page-intermediate OCR cache for all registered PDFs (V2 seam).

This is the V2 replacement for the deleted ``scripts/update_corpus.py``: it
produces the ``data/canonical/<corpus_version>-documents.jsonl`` page cache +
registry manifest that downstream ``assemble_corpus_v2`` / ``ingest_corpus_v2``
consume. It does **not** touch Qdrant, embed, or switch ``corpus-current.json``
— those are the ingest step's job.

Pipeline order for a new corpus (run from the repo root, venv active)::

    # 1. register new PDFs in the inventory
    python scripts/data_inventory.py --raw-dir data/raw --out data/registry/inventory.csv
    # 2. pre-ingestion validation (standard-number / effective_status)
    PYTHONPATH=src python scripts/audit_pdf_sources.py --write
    # 3. (optional) run formula recognition into a cache run-id
    PYTHONPATH=src python scripts/recognize_corpus_formulas.py
    # 4. THIS STEP — build the page-intermediate cache
    PYTHONPATH=src python scripts/build_corpus.py [--formula-run-id <run-id>]
    #    → writes data/canonical/<new-fingerprint>-documents.jsonl
    #      + data/registry/<new-fingerprint>.json (clean V2 manifest)
    #      + data/registry/corpus-candidate.json (candidate pointer)
    # 5. assemble V2 canonical from the candidate
    PYTHONPATH=src python scripts/assemble_corpus_v2.py --corpus-version <new-fingerprint>
    # 6. ingest into Qdrant and publish
    PYTHONPATH=src python scripts/ingest_corpus_v2.py --corpus-version <new-fingerprint> --publish
    # 7. enrich Qdrant payloads + regenerate the manifest retrieval section
    PYTHONPATH=src python scripts/enrich_corpus_v2_payloads.py
    PYTHONPATH=src python scripts/regenerate_corpus_manifest.py

The corpus fingerprint is derived from the source catalog + extractor config +
formula gate, so adding or changing a PDF yields a new version automatically.
``--plan-only`` reports the catalog without parsing.

Note: ``scripts/recognize_*.py`` are the **producers** of the formula/table
caches this step consumes via ``--formula-run-id``; they are not a parallel
path to this one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.application.corpus_update import (  # noqa: E402
    AutomaticPageExtractor,
    CorpusUpdateService,
    build_source_catalog,
)
from src.application.unified_pdf import (  # noqa: E402
    UnifiedPageExtractor,
    warm_unified_asset,
    warm_unified_preparation,
)


def _ensure_formula_cache(project_root: Path, explicit_run_id: str | None) -> str:
    """Ensure the formula recognition cache exists; return the run-id for assemble.

    ``recognize_corpus_formulas`` is the sole producer of the cache that
    ``assemble_corpus_v2`` reads. If ``--formula-run-id`` is explicitly given,
    trust it (the caller owns cache existence; assembly will hard-fail if it is
    wrong). Otherwise run ``recognize_corpus_formulas`` here — idempotent,
    ``run_batch`` skips already-cached pages — and return the config-derived
    ``run_id`` so assemble reads the same cache build just wrote.
    """
    if explicit_run_id is not None:
        print(json.dumps(
            {"stage": "formula_cache", "run_id": explicit_run_id, "source": "explicit"},
            ensure_ascii=False), flush=True)
        return explicit_run_id
    from scripts.recognize_corpus_formulas import run_corpus_formulas
    info = run_corpus_formulas(project_root)
    print(json.dumps(
        {"stage": "formula_cache", "run_id": info["run_id"], "source": "orchestrated",
         "result": info["result"]},
        ensure_ascii=False), flush=True)
    return info["run_id"]


def _refresh_inventory(project_root: Path) -> None:
    import subprocess
    subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "data_inventory.py"),
            "--raw-dir", "data/raw",
            "--out", "data/registry/inventory.csv",
        ],
        cwd=project_root,
        check=True,
    )


def plan(project_root: Path) -> dict[str, object]:
    """Report the source catalog without parsing."""
    from collections import Counter
    catalog = build_source_catalog(project_root)
    source_states = Counter(
        asset.metadata.get("source_review", "needs_review") for asset in catalog.assets
    )
    return {
        "source_file_count": catalog.source_file_count,
        "unique_content_count": catalog.unique_content_count,
        "duplicate_copy_count": catalog.duplicate_copy_count,
        "selected_content_count": len(catalog.assets),
        "same_version_copy_count": catalog.unique_content_count - len(catalog.assets),
        "unique_page_count": catalog.unique_page_count,
        "selected_page_count": sum(asset.page_count for asset in catalog.assets),
        "source_review_counts": dict(sorted(source_states.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the page-intermediate OCR cache for all registered PDFs (V2 seam)."
    )
    parser.add_argument(
        "--plan-only", action="store_true", help="refresh and report without parsing"
    )
    parser.add_argument(
        "--skip-inventory-refresh", action="store_true",
        help="use the existing inventory.csv (intended only for diagnostics)",
    )
    parser.add_argument(
        "--ocr", action=argparse.BooleanOptionalAction, default=True,
        help="OCR pages without a sufficient text layer (default: enabled)",
    )
    parser.add_argument("--minimum-native-characters", type=int, default=20)
    parser.add_argument(
        "--formula-run-id", default=None,
        help="fixed completed formula run-id; all detected regions remain source locators",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="independent PDF workers (default: 1; local structure models can use substantial memory)",
    )
    parser.add_argument(
        "--ocr-layout", action="store_true",
        help="use slower layout recognition for OCR pages",
    )
    parser.add_argument(
        "--ocr-tables", action="store_true",
        help="enable expensive table-structure recognition on every OCR page",
    )
    args = parser.parse_args(argv)

    if not args.skip_inventory_refresh:
        _refresh_inventory(_REPO_ROOT)
    catalog_report = plan(_REPO_ROOT)
    print(json.dumps({"stage": "plan", **catalog_report}, ensure_ascii=False, indent=2))
    if args.plan_only:
        return 0

    if args.ocr and args.workers == 1:
        import importlib.util
        if importlib.util.find_spec("paddleocr") is None:
            raise RuntimeError(
                "OCR is enabled but paddleocr is unavailable; install requirements-ocr.txt "
                "or use --no-ocr for a diagnostic candidate"
            )

    options = {
        "enable_ocr": args.ocr,
        "minimum_native_characters": args.minimum_native_characters,
        "table_recognition": args.ocr_tables,
        "layout_recognition": args.ocr_layout,
    }

    catalog = build_source_catalog(_REPO_ROOT)
    attempted_contents: set[str] = set()

    # Phase 1: per-page layout/text preparation (parallel-safe via warm_unified_preparation).
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        jobs = [
            executor.submit(warm_unified_preparation, _REPO_ROOT, a, n, options)
            for a in catalog.assets for n in range(1, a.page_count + 1)
        ]
        prepared = 0
        for future in as_completed(jobs):
            future.result()
            prepared += 1
            if prepared % 50 == 0 or prepared == len(jobs):
                print(json.dumps(
                    {"stage": "layout_text_prepared", "pages": prepared, "total": len(jobs)},
                    ensure_ascii=False), flush=True)

    # Phase 2: per-asset structured cache (tables / figures / formula regions).
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(warm_unified_asset, _REPO_ROOT, a, options) for a in catalog.assets]
        for future in as_completed(futures):
            result = future.result()
            attempted_contents.add(result["file_sha256"])
            print(json.dumps({"stage": "structured_asset_cached", **result}, ensure_ascii=False), flush=True)

    # Phase 2.5: ensure the formula recognition cache exists before assembly.
    # recognize_corpus_formulas is the SOLE producer of the cache that
    # assemble_corpus_v2 reads (assembly skips formula in page-intermediate
    # regions and reads only corpus_formulas/<run-id>/). If this cache is
    # missing, assembly would silently produce 0 formula elements. When
    # --formula-run-id is not explicitly given, build orchestrates recognize
    # here so there is a single entry point; the resolved run-id is printed for
    # the downstream assemble step. When --formula-run-id IS given, the caller
    # takes responsibility for the cache existing (assembly asserts if not).
    resolved_formula_run_id = _ensure_formula_cache(_REPO_ROOT, args.formula_run_id)

    # Phase 3: assemble the page-intermediate jsonl + manifest via CorpusUpdateService.
    def make_extractor() -> AutomaticPageExtractor:
        return AutomaticPageExtractor(project_root=_REPO_ROOT, **options)

    extractor = UnifiedPageExtractor(_REPO_ROOT, make_extractor())

    def report_progress(event: dict[str, object]) -> None:
        page = int(event["physical_page"])
        page_count = int(event["page_count"])
        if page == 1 or page == page_count or page % 10 == 0:
            print(json.dumps({"stage": "parse", **event}, ensure_ascii=False), flush=True)

    manifest = CorpusUpdateService(
        _REPO_ROOT,
        page_extractor=extractor,
        progress=report_progress,
        worker_count=1,
        formula_run_id=resolved_formula_run_id,
        attempted_contents=attempted_contents,
    ).build()

    summary_keys = (
        "corpus_version", "status", "source_file_count", "unique_content_count",
        "duplicate_copy_count", "selected_content_count", "same_version_copy_count",
        "page_count", "page_status_counts", "processed_content_count",
        "reused_content_count", "canonical_documents",
    )
    print(json.dumps(
        {"stage": "candidate", **{k: manifest[k] for k in summary_keys if k in manifest}},
        ensure_ascii=False, indent=2), flush=True)
    print()
    print("[build] page-intermediate cache built. Next steps:")
    print(f"  PYTHONPATH=src python scripts/assemble_corpus_v2.py --corpus-version {manifest['corpus_version']} --formula-run-id {resolved_formula_run_id}")
    print(f"  PYTHONPATH=src python scripts/ingest_corpus_v2.py --corpus-version {manifest['corpus_version']} --publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
