"""Conservative, page-local table context candidates; never automatic approval."""
import math
import re


_UNIT = re.compile(r'(?<![A-Za-z])(?:mg\s*/\s*L|g\s*/\s*L|mm|cm|m[²³23]|kPa|MPa|℃|°C|%)(?![A-Za-z])', re.I)
_CAPTION = re.compile(r'^\s*(?:续\s*)?表\s*[0-9一二三四五六七八九十]+')
_NOTE = re.compile(r'^\s*(?:注(?:\s*\d+)?\s*[:：]|说明\s*[:：])')


def _valid_box(box, page):
    try:
        return (isinstance(box, (list, tuple)) and len(box) == 4
                and all(math.isfinite(float(x)) for x in box)
                and 0 <= box[0] < box[2] <= page['width']
                and 0 <= box[1] < box[3] <= page['height'])
    except (KeyError, TypeError, ValueError):
        return False


def enrich_table_context(page, tables):
    """Attach literal candidates within 36 page points, not verified associations.

    The bounded unit lexicon is intentionally incomplete. Absence is unknown,
    not evidence that a table is dimensionless. Review is still required even
    when all candidates and cell coordinates exist.
    """
    for table in tables:
        table.update(caption_candidates=[], unit_context=[], footnotes=[])
        for cell in table['cells']:
            if _UNIT.search(cell['text']):
                table['unit_context'].append({
                    'text': cell['text'], 'physical_page': page['physical_page'],
                    'bbox': cell.get('bbox'), 'source_cell': {'row': cell['row'], 'col': cell['col']},
                    'association_status': 'pending_review', 'basis': 'literal_cell_text'})

    for element in page.get('elements', []):
        text, box = element.get('text', ''), element.get('bbox')
        if not isinstance(text, str) or not _valid_box(box, page):
            continue
        kinds = []
        if _CAPTION.search(text):
            kinds.append(('caption_candidates', 'above'))
        if _NOTE.search(text):
            kinds.append(('footnotes', 'below'))
        if _UNIT.search(text) and (text.lstrip().startswith('单位') or _NOTE.search(text)):
            kinds.append(('unit_context', 'either'))
        for kind, direction in kinds:
            candidates = []
            for table in tables:
                region = table.get('bbox')
                if not _valid_box(region, page):
                    continue
                overlap = min(box[2], region[2]) - max(box[0], region[0])
                above, below = region[1] - box[3], box[1] - region[3]
                close = ((direction in ('above', 'either') and 0 <= above <= 36)
                         or (direction in ('below', 'either') and 0 <= below <= 36))
                if overlap > 0 and close:
                    candidates.append(table)
            ids = [t['element_id'] for t in candidates]
            for table in candidates:
                table[kind].append({'text': text, 'bbox': list(box),
                    'physical_page': page['physical_page'],
                    'source_element_id': element.get('element_id'),
                    'candidate_table_ids': ids,
                    'association_status': 'ambiguous' if len(ids) > 1 else 'pending_review',
                    'basis': 'page_local_geometry_candidate'})

    for table in tables:
        reasons = list(table.get('review_reasons', []))
        located = _valid_box(table.get('bbox'), page)
        structured = bool(table['cells']) and located
        if not located:
            reasons.append('missing_table_region_coordinates')
        if not table['unit_context']:
            reasons.append('units_not_found')
        if table.get('cell_box_alignment') != 'model_order_pending_review':
            reasons.append('cell_coordinates_not_aligned')
        reasons.extend(['context_associations_unverified', 'full_table_transcription_unapproved'])
        table['missing_context'] = ['caption', 'unit_context', 'footnotes']
        if table.get('cell_box_alignment') != 'model_order_pending_review':
            table['missing_context'].append('cell_coordinates')
        table['publishable'] = False
        table['review_status'] = 'pending_review' if structured else 'source_page_only'
        table['region_quality'] = {
            'status': table['review_status'], 'can_link_source_page': True,
            'can_use_transcription': False, 'can_use_for_calculation': False,
            'reasons': list(dict.fromkeys(reasons))}
    return tables
