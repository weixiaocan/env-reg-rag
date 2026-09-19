"""Source/config identity audit of completed formula checkpoints, not approval."""
import hashlib
import json
from collections import Counter
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
