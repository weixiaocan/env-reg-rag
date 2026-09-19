"""Source-bound visual parameter reviews for full-corpus regions."""
import hashlib
import math


def attach_parameter_review(item, report, triage_sha256, page_count):
    if (not isinstance(report, dict) or report.get('schema_version') != '1'
            or report.get('triage_sha256') != triage_sha256):
        return False
    records = report.get('records')
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return False
    matches = [r for r in records if r.get('candidate_id') == item['candidate_id']]
    if len(matches) != 1:
        return False
    r = matches[0]
    if (any(r.get(k) != item.get(k) for k in ('file_sha256', 'physical_page', 'bbox'))
            or r.get('transcription_sha256') != hashlib.sha256((item.get('raw_latex') or '').encode()).hexdigest()
            or r.get('status') != 'source_visual_checked'):
        return False
    variables, conditions = r.get('variables'), r.get('conditions')
    if not isinstance(variables, list) or not 1 <= len(variables) <= 30 or not isinstance(conditions, list) or len(conditions) > 20:
        return False
    symbols = set()
    for v in variables + conditions:
        if not isinstance(v, dict):
            return False
        keys = ('symbol', 'definition', 'source_text') if v in variables else ('text', 'source_text')
        if any(not isinstance(v.get(k), str) or not v[k].strip() or len(v[k]) > 2000 for k in keys):
            return False
        page, box = v.get('physical_page'), v.get('bbox')
        if (type(page) is not int or not 1 <= page <= page_count or abs(page-item['physical_page']) > 1
                or not isinstance(box, list) or len(box) != 4
                or not all(type(n) in (int, float) and math.isfinite(n) for n in box)
                or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]):
            return False
        if v in variables:
            if v['symbol'] in symbols or not (v.get('unit') is None or isinstance(v.get('unit'), str) and v['unit'].strip()):
                return False
            symbols.add(v['symbol'])
    required = r.get('required_symbols')
    if not isinstance(required, list) or not required or not all(isinstance(s, str) and s for s in required) or len(set(required)) != len(required) or not symbols <= set(required):
        return False
    def clean(v, keys):
        return {**{k: v.get(k) for k in keys}, 'file_sha256': item['file_sha256'],
                'physical_page': v['physical_page'], 'bbox': list(v['bbox']),
                'basis': 'source_visual_checked'}
    item['reviewed_parameters'] = [clean(v, ('symbol', 'definition', 'unit', 'source_text')) for v in variables]
    item['reviewed_conditions'] = [clean(v, ('text', 'source_text')) for v in conditions]
    item['parameter_missing_symbols'] = sorted(set(required)-symbols)
    item['parameter_status'] = 'source_visual_checked'
    return True
