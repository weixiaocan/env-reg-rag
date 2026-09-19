"""Read a fixed layout run only to downgrade regions, never grant approval."""
import hashlib
import json
import math
import re
from src.evaluation.formula_checkpoint_audit import audit_checkpoints


def load_formula_region_gate(root, run_id, catalog):
    if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{16}', run_id):
        raise ValueError('invalid formula run id')
    folder = root/'data/model_runtime/corpus_formulas'/run_id
    path = folder/'triage.json'
    if (not folder.resolve().is_relative_to(root/'data/model_runtime')
            or path.is_symlink() or (folder/'run.lock').exists()
            or path.stat().st_size > 5000000):
        raise ValueError('unsafe or running formula batch')
    payload = path.read_bytes(); report = json.loads(payload)
    fingerprint = report.get('config_hash')
    if (report.get('schema_version') != '1' or report.get('review_status') != 'pending_review'
            or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}',fingerprint)
            or not fingerprint.startswith(run_id)
            or type(report.get('document_count')) is not int or report['document_count'] != len(catalog.assets)
            or type(report.get('page_count')) is not int or report['page_count'] != sum(a.page_count for a in catalog.assets)
            or not isinstance(report.get('regions'),list)
            or type(report.get('region_count')) is not int or report['region_count'] != len(report['regions'])
            or not isinstance(report.get('checkpoint_manifest_sha256'),str)
            or not re.fullmatch('[0-9a-f]{64}', report['checkpoint_manifest_sha256'])):
        raise ValueError('invalid formula run coverage/provenance')
    fresh = audit_checkpoints(folder, [(a.sha256,p) for a in catalog.assets for p in range(1,a.page_count+1)], fingerprint)
    if fresh['regions'] != report['regions'] or fresh['checkpoint_manifest_sha256'] != report['checkpoint_manifest_sha256']:
        raise ValueError('stale formula region report')
    counts = {a.sha256: a.page_count for a in catalog.assets}
    regions = []; identities = set()
    for r in report['regions']:
        if not isinstance(r, dict):
            raise ValueError('invalid formula region')
        sha, page, identity, box = r.get('sha256'), r.get('physical_page'), r.get('candidate_id'), r.get('bbox')
        if (not isinstance(sha,str) or sha not in counts or type(page) is not int or not 1 <= page <= counts[sha]
                or not isinstance(identity,str) or not re.fullmatch(f'layout-{sha[:16]}-{page}-[0-9]+',identity)
                or identity in identities
                or not isinstance(box,list) or len(box)!=4
                or not all(type(v) in (int,float) and math.isfinite(v) for v in box)
                or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]):
            raise ValueError('invalid formula region identity/coordinates')
        identities.add(identity)
        # No raw LaTeX, arbitrary paths or asserted approval enter evidence.
        regions.append({'file_sha256': sha, 'physical_page': page,
                        'element_id': 'protected-'+identity, 'bbox': list(box)})
    return {'regions': regions, 'provenance': {'run_id':run_id, 'report_sha256':hashlib.sha256(payload).hexdigest(),
            'config_hash':fingerprint, 'checkpoint_manifest_sha256':report['checkpoint_manifest_sha256'],
            'region_count':len(regions), 'policy':'detected-formula-regions-locator-only-v1',
            'proves_transcription_correctness':False, 'can_use_for_calculation':False}}
