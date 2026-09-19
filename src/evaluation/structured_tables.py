"""Cell-bound diagnostic evaluation; never an automatic approval decision."""
from __future__ import annotations

import re


def _text(value):
    # Only presentation differences: preserve decimal points, signs and units.
    return re.sub(r'\s+', '', value.replace('≥', '>=').replace('≤', '<='))


def _valid(cell):
    return (isinstance(cell, dict)
            and all(type(cell.get(k)) is int and cell[k] >= 0 for k in ('row', 'col'))
            and all(type(cell.get(k, 1)) is int and 1 <= cell.get(k, 1) <= 1000
                    for k in ('row_span', 'col_span'))
            and isinstance(cell.get('text'), str))


def evaluate_cells(cells, expected):
    """Check gold cells in the same table coordinates, including merged cells.

    This is a partial gold comparison, not full-table quality certification.
    Missing structure never falls back to flattened page text.
    """
    if (not isinstance(expected, list) or not expected
            or any(not _valid(c) or not _text(c['text']) for c in expected)
            or len({(c['row'], c['col']) for c in expected}) != len(expected)):
        raise ValueError('gold cells require unique coordinates and nonempty text')
    invalid = not isinstance(cells, list) or any(not _valid(c) for c in cells)
    if not invalid:
        # Rectangle intersection avoids materializing large merged-cell grids.
        for i, left in enumerate(cells):
            for right in cells[i + 1:]:
                if (left['row'] < right['row'] + right.get('row_span', 1)
                        and right['row'] < left['row'] + left.get('row_span', 1)
                        and left['col'] < right['col'] + right.get('col_span', 1)
                        and right['col'] < left['col'] + left.get('col_span', 1)):
                    invalid = True
                    break
            if invalid:
                break
    checks = []
    for gold in expected:
        matches = [] if invalid else [c for c in cells
                    if c['row'] <= gold['row'] < c['row'] + c.get('row_span', 1)
                    and c['col'] <= gold['col'] < c['col'] + c.get('col_span', 1)]
        actual = matches[0]['text'] if len(matches) == 1 else None
        passed = actual is not None and _text(actual) == _text(gold['text'])
        checks.append({'row': gold['row'], 'col': gold['col'], 'expected': gold['text'],
                       'actual': actual, 'passed': passed,
                       'reason': 'invalid_structure' if invalid else
                                 'missing_cell' if actual is None else
                                 'match' if passed else 'cell_text_mismatch'})
    return {'comparison': 'exact_normalized_text_at_gold_cell_coordinates',
            'check_count': len(checks),
            'passed_check_count': sum(c['passed'] for c in checks),
            'all_checks_passed': all(c['passed'] for c in checks), 'checks': checks,
            'can_approve_transcription': False,
            'limitation': 'Diagnostic subset only; does not approve whole-table OCR or calculations.'}
