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
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.recognize_page_formulas import recognize_pages, model_provenance
from src.application.corpus_update import build_source_catalog
from src.application.formula_batch import run_batch
from src.evaluation.formula_checkpoint_audit import refresh_triage_for_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.ingestion.formula_layout import CachedLayoutEngine
from src.ingestion.formula_ocr import CachedFormulaEngine
from src.ingestion.formula_supplements import registry_digest


def _reuse_unchanged_layout_checkpoints(project_root: Path, jobs: list[tuple[str, int]],
                                        directory: Path, old_hash: str, new_hash: str,
                                        affected: set[tuple[str, int]], provenance: dict) -> int:
    """Rebind local model results only where the source page has no supplement.

The model, source checksum and old run identity must match exactly. A reused
    record names its original checkpoint digest; supplemented pages are always
    inferred afresh from the local PDF crop.
    """
    source_dir = project_root / 'data/model_runtime/corpus_formulas' / old_hash[:16]
    if not source_dir.is_dir():
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for sha, page in jobs:
        if (sha, page) in affected:
            continue
        target = directory / f'{sha}-{page}.json'
        if target.exists():
            continue
        source = source_dir / target.name
        if not source.is_file() or source.is_symlink() or target.is_symlink():
            continue
        payload = source.read_bytes()
        try:
            old = json.loads(payload)
            result = old['result']
            pages = result['pages']
            valid = (old.get('config_hash') == old_hash and old.get('status') == 'completed'
                     and old.get('sha256') == sha and old.get('physical_page') == page
                     and result.get('sha256') == sha
                     and result.get('profile') == 'layout-formula-v1:200dpi:score0.5'
                     and result.get('model_checksums') == provenance['model_checksums']
                     and result.get('package_versions') == provenance['package_versions']
                     and result.get('layout_supplements_sha256') is None
                     and len(pages) == 1 and pages[0].get('physical_page') == page
                     and all(r.get('sha256') == sha and r.get('physical_page') == page
                             and not r.get('failure') for r in pages[0].get('regions', [])))
        except (KeyError, TypeError, ValueError):
            continue
        if not valid:
            continue
        result['layout_supplements_sha256'] = registry_digest(project_root)
        old['config_hash'] = new_hash
        old['reused_from_checkpoint_sha256'] = hashlib.sha256(payload).hexdigest()
        old['reused_from_run_id'] = old_hash[:16]
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, target)
        count += 1
    return count


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
    config = {'profile': 'layout-formula-v1:200dpi:score0.5:padding2point', **provenance,
              'layout_supplements_sha256': registry_digest(project_root)}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    previous_config = {'profile': config['profile'], **provenance}
    previous_hash = hashlib.sha256(json.dumps(previous_config, sort_keys=True).encode()).hexdigest()
    jobs = [(a.sha256, p) for a in assets.values() for p in range(1, a.page_count + 1)]
    if limit_pages:
        jobs = jobs[:limit_pages]
    directory = project_root / 'data/model_runtime/corpus_formulas' / config_hash[:16]
    if not directory.resolve().is_relative_to(project_root / 'data/model_runtime'):
        raise ValueError('output directory escape')
    print(json.dumps({'selected_documents': len(assets), 'scheduled_pages': len(jobs),
                      'config_hash': config_hash}), flush=True)
    if previous_hash != config_hash:
        supplement_path = project_root / 'data/registry/formula-layout-supplements.json'
        entries = json.loads(supplement_path.read_text(encoding='utf-8'))['regions'] if supplement_path.is_file() else []
        affected = {(row['source_sha256'], row['physical_page']) for row in entries}
        reused = _reuse_unchanged_layout_checkpoints(project_root, jobs, directory,
            previous_hash, config_hash, affected, provenance)
        print(json.dumps({'stage': 'formula_checkpoint_reuse', 'pages': reused,
                          'source_run_id': previous_hash[:16]}), flush=True)
    result = run_batch(directory, jobs, config_hash,
                       lambda sha, page: recognize_pages(project_root, sha, [page], layout=layout,
                                                         recognizer=recognizer, asset=assets[sha],
                                                         provenance=provenance))
    for asset in assets.values():
        if file_digest(registered_pdf_path(project_root, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source changed during batch')
    # Full-catalog runs must rewrite triage so build's formula gate sees current
    # document/page coverage (stale triage after adding PDFs used to fail closed).
    triage = None
    if not limit_pages:
        triage = refresh_triage_for_catalog(project_root, config_hash[:16], catalog=catalog)
    return {'run_id': config_hash[:16], 'config_hash': config_hash,
            'directory': str(directory), 'result': result,
            'triage': None if triage is None else {
                'document_count': triage['document_count'],
                'page_count': triage['page_count'],
                'region_count': triage['region_count'],
            }}


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
