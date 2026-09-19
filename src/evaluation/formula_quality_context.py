"""Source-bound OCR reading cues; never semantic approval or calculation."""
import hashlib
import math


def valid_box(box, width, height):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def build_context(item, pages):
    number, box = item['physical_page'], item['bbox']
    by_page = {p['physical_page']: p for p in pages}
    if len(by_page) != len(pages) or number not in by_page:
        raise ValueError('missing or duplicate source page')
    current = by_page[number]
    if not valid_box(box, current['width'], current['height']):
        raise ValueError('invalid formula crop')
    contexts = []
    for n in range(max(1, number-1), number+2):
        if n not in by_page:
            continue
        page = by_page[n]; width, height = page['width'], page['height']
        top, bottom = (max(0, height-240), height) if n < number else (0, min(height, 240)) if n > number else (max(0, box[1]-100), min(height, box[3]+240))
        excerpts = []
        for element in page.get('elements', []):
            b, text = element.get('bbox'), element.get('text')
            if not valid_box(b, width, height) or not isinstance(text, str) or not text.strip() or b[3] < top or b[1] > bottom:
                continue
            if len(excerpts) < 60:
                excerpts.append({'text': text[:4000], 'bbox': b, 'physical_page': n, 'verified': False})
        contexts.append({'physical_page': n, 'relation': 'current' if n == number else 'previous' if n < number else 'next',
                         'source_url': f'/api/v1/documents/doc_{item["sha256"][:16]}/content#page={n}',
                         'extraction_route': page.get('extraction_route', 'unknown'),
                         'page_quality': page.get('decision_status', 'unknown'),
                         'status': 'unverified_canonical_text' if excerpts else 'no_canonical_text_in_window',
                         'verified': False, 'excerpts': excerpts})
    return {'candidate_id': item['candidate_id'], 'file_sha256': item['sha256'], 'physical_page': number,
            'transcription_sha256': hashlib.sha256((item.get('raw_latex') or '').encode()).hexdigest(),
            'source_context': contexts, 'review_status': 'pending_review', 'publishable': False,
            'can_use_for_calculation': False,
            'blockers': ['manual_transcription_review', 'verified_variables', 'verified_units', 'verified_conditions']}
