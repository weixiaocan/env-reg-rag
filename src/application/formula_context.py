"""Join explicit visual-context reviews, without inference or execution approval."""
import copy
import hashlib
import math


def _box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(x) in (int, float) and math.isfinite(x) for x in box)
            and 0 <= box[0] < box[2] and 0 <= box[1] < box[3])


def attach_reviewed_context(result, records):
    result.update(variable_definitions=[], units=[], conditions=[],
                  context_review={'status': 'not_matched'},
                  publishable=False, can_use_for_calculation=False)
    result['missing_context'] = ['verified_variables', 'verified_units', 'verified_conditions']
    latex = result.get('raw_latex')
    if not isinstance(latex, str) or not latex.strip() or result.get('status') != 'pending_review':
        return result
    keys = ('anchor_id', 'file_sha256', 'physical_page', 'formula_number', 'processing_profile')
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        result['context_review']['status'] = 'invalid_review'
        return result
    matches = [r for r in records if type(r.get('physical_page')) is int
               and all(r.get(k) == result.get(k) for k in keys)
               and r.get('transcription_sha256') == hashlib.sha256(latex.encode('utf-8')).hexdigest()]
    if len(matches) != 1:
        result['context_review']['status'] = 'ambiguous' if matches else 'not_matched'
        return result
    record = matches[0]
    region = record.get('source_bbox')
    source_region = (result.get('context_evidence') or {}).get('bbox')
    if (record.get('review_status') != 'manual_visual_confirmed'
            or not _box(region) or not _box(source_region)
            or not (source_region[0] <= region[0] < region[2] <= source_region[2]
                    and source_region[1] <= region[1] < region[3] <= source_region[3])):
        result['context_review']['status'] = 'invalid_or_unreviewed'
        return result
    variables, conditions, required = (record.get(k) for k in ('variables', 'conditions', 'required_symbols'))
    if (not isinstance(variables, list) or not isinstance(conditions, list)
            or not isinstance(required, list) or not required
            or not all(isinstance(s, str) and s for s in required) or len(required) != len(set(required))
            or not all(isinstance(v, dict) and all(isinstance(v.get(k), str) and v[k].strip()
                          for k in ('symbol', 'definition', 'source_text'))
                          and ((isinstance(v.get('unit'), str) and v['unit'].strip())
                               or (v.get('unit') is None and v.get('unit_status') == 'not_stated'))
                          for v in variables)
            or not all(isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k].strip()
                          for k in ('text', 'source_text')) for c in conditions)):
        result['context_review']['status'] = 'invalid_review'
        return result
    symbols = [v['symbol'] for v in variables]
    if len(symbols) != len(set(symbols)) or not set(symbols) <= set(required):
        result['context_review']['status'] = 'invalid_review'
        return result
    evidence = {'file_sha256': result['file_sha256'], 'physical_page': result['physical_page'],
                'bbox': list(region), 'basis': 'manual_visual_source_page_review',
                'review_status': 'manual_visual_confirmed'}
    result['variable_definitions'] = [{**copy.deepcopy(v), **evidence} for v in variables]
    result['units'] = [{'symbol': v['symbol'], 'unit': v['unit'],
                        'unit_status': 'stated' if v['unit'] is not None else 'not_stated',
                        'source_text': v['source_text'],
                        **evidence} for v in variables]
    result['conditions'] = [{**copy.deepcopy(c), **evidence} for c in conditions]
    missing = sorted(set(required) - set(symbols))
    result['context_review'] = {'status': 'matched', 'missing_symbols': missing,
                                'condition_scope': 'explicit_local_notes_only',
                                'all_applicability_conditions_verified': False}
    if not missing:
        result['missing_context'].remove('verified_variables')
        if all(v['unit'] is not None for v in variables):
            result['missing_context'].remove('verified_units')
    # Nearby notes never establish all applicability conditions, nor approval.
    return result
