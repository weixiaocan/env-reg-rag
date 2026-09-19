"""Original, numbered cross-page formula references; never inferred definitions."""
import re


NUMBERED_MARKER = re.compile(r'式\s*[（(]?\s*(\d+(?:[.－-]\d+)*)\s*[)）]?\s*中')


def attach_cross_page_formula_sources(document):
    formulas = [r for p in document['pages'] for r in p.get('regions', [])
                if r['kind'] == 'formula' and r.get('bbox')
                and r.get('math_category') == 'relation_candidate']
    by_number = {}
    for formula in formulas:
        for number in {c['number'].replace('－', '-') for c in formula.get('formula_number_candidates', [])}:
            by_number.setdefault(number, []).append(formula)
    by_page = {p['physical_page']: p for p in document['pages']}
    for page in document['pages']:
        elements = sorted((e for e in page.get('elements', []) if e.get('bbox') and e.get('text')),
                          key=lambda e: (e['bbox'][1], e['bbox'][0]))
        for element in elements:
            text = str(element['text'])
            for match in NUMBERED_MARKER.finditer(text):
                number = match.group(1).replace('－', '-')
                targets = by_number.get(number, [])
                source = {'physical_page': page['physical_page'], 'element_id': element['element_id'],
                          'bbox': list(element['bbox']), 'text': text,
                          'character_span': [match.start(), match.end()]}
                if len(targets) != 1:
                    for formula in targets:
                        formula['issues'].append('numbered_parameter_reference_not_unique:page=' + str(page['physical_page']))
                        formula['relations'].append({'kind': 'ambiguous_numbered_formula_parameter_reference',
                            'formula_number': number, 'source': source, 'verified': False,
                            'association_status': 'uncertain'})
                    continue
                formula = targets[0]
                if formula['physical_page'] == page['physical_page']:
                    continue
                formula['relations'].append({'kind': 'explicit_numbered_formula_parameter_reference',
                    'formula_number': number, 'source': source, 'verified': False,
                    'basis': 'literal_numbered_parameter_marker_unique_formula_in_document'})
                excerpt = {**source, 'verified': False,
                           'association_basis': 'literal_numbered_parameter_marker_unique_formula_in_document'}
                reading = formula.setdefault('parameter_reading', {'excerpts': []})
                reading['excerpts'].append(excerpt)
                reading.update(status='source_excerpts_pending_review',
                               association='same_page_or_explicit_numbered_cross_page_marker')
                formula['context'].append(excerpt)
        # Bare '式中' does not establish which formula it belongs to. Preserve
        # a concrete source candidate without adding it to associated text.
        content = [e for e in elements if not re.fullmatch(r'\s*\d+\s*', str(e['text']))]
        marker = next((e for e in content if re.match(r'^\s*式\s*中\s*[:：]', str(e['text']))), None)
        if marker is None or marker != content[0] or page['physical_page']-1 not in by_page:
            continue
        preceding = [r for r in formulas if r['physical_page'] == page['physical_page']-1]
        if not preceding:
            continue
        bottom = max(r['bbox'][3] for r in preceding)
        last = [r for r in preceding if abs(r['bbox'][3]-bottom) < 1]
        for formula in last:
            formula['issues'].append('next_page_parameter_marker_without_formula_number:page=' + str(page['physical_page']))
            formula['relations'].append({'kind': 'uncertain_next_page_parameter_marker',
                'source': {'physical_page': page['physical_page'], 'element_id': marker['element_id'],
                           'bbox': list(marker['bbox']), 'text': str(marker['text'])},
                'verified': False, 'association_status': 'uncertain',
                'basis': 'bare_original_marker_on_following_page_has_no_formula_identifier'})
