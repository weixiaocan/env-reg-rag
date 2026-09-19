"""Build full formula OCR context coverage without modifying PDFs or publishing."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.application.corpus_artifacts import resolve_current_corpus
from src.application.corpus_update import build_source_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.evaluation.formula_quality_context import build_context
from src.evaluation.formula_checkpoint_audit import audit_checkpoints


def audit(root, run_id):
    root = Path(root).resolve()
    if not re.fullmatch('[0-9a-f]{16}', run_id):
        raise ValueError('invalid run id')
    folder = root / 'data/model_runtime/corpus_formulas' / run_id
    if not folder.resolve().is_relative_to(root / 'data/model_runtime') or (folder / 'run.lock').exists():
        raise ValueError('unsafe or running batch')
    catalog = build_source_catalog(root)
    for asset in catalog.assets:
        if file_digest(registered_pdf_path(root, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source checksum mismatch')
    triage_path = folder / 'triage.json'
    if triage_path.is_symlink():
        raise ValueError('symlink triage')
    triage_bytes = triage_path.read_bytes(); triage = json.loads(triage_bytes)
    fresh = audit_checkpoints(folder, [(a.sha256, p) for a in catalog.assets for p in range(1, a.page_count+1)], triage['config_hash'])
    if fresh['regions'] != triage['regions'] or fresh['checkpoint_manifest_sha256'] != triage['checkpoint_manifest_sha256']:
        raise ValueError('stale triage')
    current = resolve_current_corpus(root)
    manifest = json.loads(current.manifest_path.read_text(encoding='utf-8'))
    path = root / manifest['canonical_documents']
    if not path.resolve().is_relative_to(root / 'data/canonical') or path.is_symlink():
        raise ValueError('canonical path escape')
    data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
    if digest != manifest['canonical_documents_sha256']:
        raise ValueError('canonical checksum mismatch')
    # Split only physical JSONL newlines, not Unicode separators inside strings.
    documents = {}
    for line in data.decode('utf-8').split('\n'):
        if line.strip():
            document = json.loads(line)
            if document['sha256'] in documents:
                raise ValueError('duplicate canonical document')
            documents[document['sha256']] = document
    for asset in catalog.assets:
        pages = documents[asset.sha256]['pages']
        numbers = [p['physical_page'] for p in pages]
        if (any(type(n) is not int for n in numbers)
                or sorted(numbers) != list(range(1, asset.page_count+1))):
            raise ValueError('canonical page coverage mismatch')
    records = [build_context(item, documents[item['sha256']]['pages']) for item in fresh['regions']]
    counts = Counter()
    for record in records:
        page = next(p for p in record['source_context'] if p['relation'] == 'current')
        counts['with_current_text' if page['excerpts'] else 'without_current_text'] += 1
        counts['current_page_quality_' + page['page_quality']] += 1
        counts['current_route_' + page['extraction_route']] += 1
    for asset in catalog.assets:
        if file_digest(registered_pdf_path(root, asset.canonical_rel_path)) != asset.sha256:
            raise ValueError('source changed during audit')
    return {'schema_version': '1', 'corpus_version': current.corpus_version,
            'canonical_sha256': digest, 'triage_sha256': hashlib.sha256(triage_bytes).hexdigest(),
            'region_count': len(records), 'counts': dict(counts), 'status': 'pending_review',
            'publishable': False, 'can_use_for_calculation': False, 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(); report = audit(ROOT, args.run_id)
    target = ROOT / 'data/model_runtime/corpus_formulas' / args.run_id / 'quality-context.json'
    if target.is_symlink():
        raise ValueError('symlink output')
    temporary = target.with_suffix('.tmp')
    if temporary.is_symlink():
        raise ValueError('symlink temporary')
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)
    print(json.dumps({k:v for k,v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
