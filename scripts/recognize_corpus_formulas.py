"""Resumable local formula scan over every selected primary PDF page.

This is the **sole producer** of the formula recognition cache that
``assemble_corpus_v2`` reads via ``load_formula_pages``. Assembly does NOT read
formula regions from the page-intermediate ``regions`` list (those are skipped
at ``canonical_assembly._collect_page_items``) — it reads only this cache. So
skipping this step makes assembly silently produce 0 formula elements.

``build_corpus`` now orchestrates this internally via ``run_corpus_formulas``
when ``--formula-run-id`` is not explicitly given, so the cache is guaranteed
present before assembly. This script remains as the standalone entry point for
re-runs / diagnostics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recognize_page_formulas import recognize_pages, model_provenance
from src.application.corpus_update import build_source_catalog
from src.application.formula_batch import run_batch
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.ingestion.formula_layout import CachedLayoutEngine
from src.ingestion.formula_ocr import CachedFormulaEngine


def run_corpus_formulas(project_root: Path, limit_pages: int | None = None) -> dict:
    """Run the corpus-wide formula recognition; return the run-id + result.

    Idempotent: ``run_batch`` skips already-cached pages (no re-inference), so
    re-running over a complete cache is cheap file IO. The returned ``run_id``
    (``config_hash[:16]``) is exactly what ``assemble_corpus_v2 --formula-run-id``
    must consume so assembly reads the same cache this step wrote.

    Returns ``{"run_id", "config_hash", "directory", "result"}``.
    """
    catalog = build_source_catalog(project_root)
    assets = {a.sha256: a for a in catalog.assets}
    # Verify every selected source, including pages that would be reused.
    for asset in assets.values():
        if file_digest(registered_pdf_path(project_root, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source checksum mismatch')
    layout, recognizer = CachedLayoutEngine(), CachedFormulaEngine()
    provenance = model_provenance(layout, recognizer)
    config = {'profile': 'layout-formula-v1:200dpi:score0.5:padding2point', **provenance}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    jobs = [(a.sha256, p) for a in assets.values() for p in range(1, a.page_count + 1)]
    if limit_pages:
        jobs = jobs[:limit_pages]
    directory = project_root / 'data/model_runtime/corpus_formulas' / config_hash[:16]
    if not directory.resolve().is_relative_to(project_root / 'data/model_runtime'):
        raise ValueError('output directory escape')
    print(json.dumps({'selected_documents': len(assets), 'scheduled_pages': len(jobs),
                      'config_hash': config_hash}), flush=True)
    result = run_batch(directory, jobs, config_hash,
                       lambda sha, page: recognize_pages(project_root, sha, [page], layout=layout,
                                                         recognizer=recognizer, asset=assets[sha],
                                                         provenance=provenance))
    for asset in assets.values():
        if file_digest(registered_pdf_path(project_root, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source changed during batch')
    return {'run_id': config_hash[:16], 'config_hash': config_hash,
            'directory': str(directory), 'result': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit-pages', type=int, help='Explicit bounded smoke test; omit for all pages')
    args = parser.parse_args()
    if args.limit_pages is not None and args.limit_pages < 1:
        parser.error('limit-pages must be positive')
    info = run_corpus_formulas(ROOT, limit_pages=args.limit_pages)
    print(json.dumps({'run_id': info['run_id'], 'result': info['result']}), flush=True)


if __name__ == '__main__':
    main()
