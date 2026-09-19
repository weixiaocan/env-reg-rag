"""Location-only diagnostic; never use answer/transcription gold to detect regions."""
import math


def _valid(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] and 0 <= box[1] < box[3])


def evaluate_formula_locations(candidates, anchors):
    rows = []
    ids = set()
    for anchor in anchors:
        if anchor['anchor_id'] in ids or not _valid(anchor.get('bbox')):
            raise ValueError('invalid or duplicate evaluation anchor')
        ids.add(anchor['anchor_id'])
        box = anchor['bbox']
        area = (box[2] - box[0]) * (box[3] - box[1])
        page_candidates = [c for c in candidates if c.get('kind') == 'formula'
                           and c.get('sha256') == anchor['file_sha256']
                           and c.get('physical_page') == anchor['physical_page']]
        scores = []
        for candidate in page_candidates:
            other = candidate.get('bbox')
            if not _valid(other):
                continue
            intersection = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(0, min(box[3], other[3]) - max(box[1], other[1]))
            other_area = (other[2] - other[0]) * (other[3] - other[1])
            scores.append((intersection / area, intersection / (area + other_area - intersection), candidate['candidate_id']))
        best = max(scores, default=(0, 0, None))
        rows.append({'anchor_id': anchor['anchor_id'], 'physical_page': anchor['physical_page'],
                     'page_signal': bool(page_candidates), 'best_anchor_coverage': best[0],
                     'best_iou': best[1], 'candidate_id': best[2],
                     'located_by_block': best[0] >= .8})
    return {'schema_version': '1', 'metric': 'formula-anchor-block-coverage-v1',
            'coverage_threshold': .8, 'anchor_count': len(rows),
            'located_anchor_count': sum(r['located_by_block'] for r in rows),
            'page_signal_anchor_count': sum(r['page_signal'] for r in rows),
            'full_corpus_quality_established': False, 'anchors': rows}
