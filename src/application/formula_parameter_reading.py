"""Literal, spatially bounded parameter reading aids, never inferred definitions."""
import re


def parameter_reading(contexts, page, bbox, next_formula_top=None):
    """Keep an explicit '式中' block below this region, stopping at next formula.

    OCR lines remain unverified source excerpts. Units and symbol semantics are
    intentionally not reconstructed from damaged text or neighbouring formulas.
    """
    output = []
    started = False
    for context in contexts:
        if context['physical_page'] != page:
            continue
        excerpts = sorted(context['excerpts'], key=lambda e: (e['bbox'][1], e['bbox'][0]))
        for excerpt in excerpts:
            box = excerpt['bbox']
            if box[3] < bbox[3] - 8:
                continue
            if next_formula_top is not None and box[3] >= next_formula_top:
                break
            text = excerpt['text']
            if not started:
                if not re.search(r'式\s*中\s*[:：]?', text):
                    continue
                started = True
            elif re.match(r'^\s*\d+(?:\.\d+){2,}\s*', text):
                break
            output.append({'text': text, 'physical_page': page,
                           'bbox': list(box), 'verified': False})
            if len(output) == 30:
                break
    return {'status': 'source_excerpts_pending_review' if output else 'not_located',
            'association': 'same_page_explicit_marker_only', 'excerpts': output}
