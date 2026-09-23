"""Source/config identity audit of completed formula checkpoints, not approval."""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from src.evaluation.formula_triage import classify_math_region


def audit_checkpoints(directory, jobs, config_hash):
    items, digests, ids = [], [], set()
    for sha, page in jobs:
        path = directory / f'{sha}-{page}.json'
        if path.is_symlink():
            raise ValueError('symlink checkpoint')
        payload = path.read_bytes()
        digests.append(hashlib.sha256(payload).hexdigest())
        record = json.loads(payload)
        if (record.get('sha256'), record.get('physical_page'), record.get('config_hash'), record.get('status')) != (sha, page, config_hash, 'completed'):
            raise ValueError('checkpoint identity/status mismatch')
        result = record['result']
        provenance = {'profile': 'layout-formula-v1:200dpi:score0.5:padding2point',
                      'model_checksums': result['model_checksums'], 'package_versions': result['package_versions']}
        if hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest() != config_hash:
            raise ValueError('checkpoint provenance mismatch')
        if (result.get('sha256') != sha or result.get('coordinate_system') != 'page_points_top_left'
                or result.get('profile') != 'layout-formula-v1:200dpi:score0.5'):
            raise ValueError('result source/coordinates mismatch')
        pages = result['pages']
        if len(pages) != 1 or pages[0]['physical_page'] != page:
            raise ValueError('result page mismatch')
        for region in pages[0]['regions']:
            if region.get('sha256') != sha or region.get('physical_page') != page or region.get('failure'):
                raise ValueError('region source/status mismatch')
            identity = region['candidate_id']
            if identity in ids:
                raise ValueError('duplicate region identity')
            ids.add(identity)
            items.append({'candidate_id': identity, 'sha256': sha, 'physical_page': page,
                          'bbox': region.get('bbox'), 'crop_bbox': region.get('crop_bbox'),
                          'coordinate_system': 'page_points_top_left', 'raw_latex': region.get('raw_latex'),
                          'detection_score': region.get('score'), **classify_math_region(region.get('raw_latex'))})
    return {'schema_version': '1', 'config_hash': config_hash, 'page_count': len(jobs),
            'checkpoint_manifest_sha256': hashlib.sha256(''.join(digests).encode()).hexdigest(),
            'region_count': len(items), 'counts': dict(Counter(i['category'] for i in items)),
            'review_status': 'pending_review', 'publishable': False,
            'can_use_for_calculation': False, 'regions': items}


def write_triage_report(directory, jobs, config_hash, document_count):
    """Rebuild triage.json from completed checkpoints for the given job set.

    `document_count` must match the current source catalog size; the formula
    gate rejects triage whose document/page coverage does not match the catalog
    even when every checkpoint file is present.
    """
    if type(document_count) is not int or document_count < 1:
        raise ValueError('invalid document_count')
    directory = Path(directory)
    report = audit_checkpoints(directory, jobs, config_hash)
    report['document_count'] = document_count
    target = directory / 'triage.json'
    if target.is_symlink() or target.with_suffix('.tmp').is_symlink():
        raise ValueError('symlink report target')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        os.replace(temporary, target)
    except PermissionError:
        if target.exists():
            target.unlink()
        os.replace(temporary, target)
    return report


def resolve_formula_config_hash(directory, run_id):
    """Read config_hash for a formula run folder; must start with run_id."""
    import re
    directory = Path(directory)
    if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{16}', run_id):
        raise ValueError('invalid formula run id')
    for name in ('summary.json', 'triage.json'):
        path = directory / name
        if not path.is_file() or path.is_symlink():
            continue
        payload = json.loads(path.read_text(encoding='utf-8'))
        fingerprint = payload.get('config_hash')
        if isinstance(fingerprint, str) and fingerprint.startswith(run_id):
            return fingerprint
    raise ValueError('formula run config_hash unavailable')


def refresh_triage_for_catalog(project_root, run_id, catalog=None):
    """Rewrite triage.json so coverage matches the current source catalog.

    Call after formula recognition, and again before build consumes an explicit
    `--formula-run-id`, so adding PDFs cannot leave a stale 21-doc triage in
    front of a 23-doc catalog.
    """
    from src.application.corpus_update import build_source_catalog

    project_root = Path(project_root).resolve()
    if catalog is None:
        catalog = build_source_catalog(project_root)
    directory = project_root / 'data/model_runtime/corpus_formulas' / run_id
    if not directory.resolve().is_relative_to(project_root / 'data/model_runtime'):
        raise ValueError('run directory escape')
    if (directory / 'run.lock').exists():
        raise ValueError('batch may still be running')
    config_hash = resolve_formula_config_hash(directory, run_id)
    jobs = [(a.sha256, p) for a in catalog.assets for p in range(1, a.page_count + 1)]
    return write_triage_report(directory, jobs, config_hash, document_count=len(catalog.assets))
