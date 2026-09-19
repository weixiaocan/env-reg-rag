"""Check full batch identity/completeness and route every mathematical region for review."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.corpus_update import build_source_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.evaluation.formula_triage import classify_math_region


from src.evaluation.formula_checkpoint_audit import audit_checkpoints


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{16}', args.run_id):
        raise ValueError('invalid run id')
    directory = ROOT / 'data/model_runtime/corpus_formulas' / args.run_id
    if not directory.resolve().is_relative_to(ROOT / 'data/model_runtime'):
        raise ValueError('run directory escape')
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    if (directory / 'run.lock').exists():
        raise ValueError('batch may still be running')
    if summary.get('status') != 'finished_pending_review' or summary.get('failed_pages') != 0:
        raise ValueError('batch not complete without execution failures')
    catalog = build_source_catalog(ROOT)
    jobs = [(a.sha256, p) for a in catalog.assets for p in range(1, a.page_count + 1)]
    for asset in catalog.assets:
        if file_digest(registered_pdf_path(ROOT, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source checksum mismatch')
    report = audit_checkpoints(directory, jobs, summary['config_hash'])
    if report['page_count'] != summary['total_pages'] or report['page_count'] != summary['processed_pages'] or report['region_count'] != summary['region_count']:
        raise ValueError('summary coverage mismatch')
    report['document_count'] = len(catalog.assets)
    for asset in catalog.assets:
        if file_digest(registered_pdf_path(ROOT, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source changed during audit')
    target = directory / 'triage.json'
    if target.is_symlink():
        raise ValueError('symlink report target')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'regions'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
