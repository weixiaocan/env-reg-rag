"""Bounded formula-display diagnostics, not LaTeX parsing or math equivalence."""
import re


def _compact(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        return None
    return re.sub(r'\s+', '', value)


def _presentation(value):
    # Only the two observed sizing commands, not arbitrary left*/right* macros.
    value = value.replace(r'\left(', '(').replace(r'\right)', ')')
    # Text/roman wrappers are removed only for exclusively CJK literal labels.
    return re.sub(r'\\(?:text|mathrm)\{([\u3400-\u9fff]+)\}', r'\1', value)


def evaluate_formula_display(actual, expected):
    a, e = _compact(actual), _compact(expected)
    valid = a is not None and e is not None
    return {'exact_display_match': bool(valid and a == e),
            'presentation_normalized_match': bool(valid and _presentation(a) == _presentation(e)),
            'normalization_scope': 'whitespace_parenthesis_sizing_cjk_text_wrappers',
            'diagnostic_only': True, 'proves_mathematical_equivalence': False}
