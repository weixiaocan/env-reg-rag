"""Conservative review routing, not LaTeX validation or formula approval."""
import re


def classify_math_region(latex):
    flags = []
    category = 'needs_review'
    if not isinstance(latex, str) or not latex.strip() or len(latex) > 10000:
        flags.append('missing_or_oversized_transcription')
    else:
        depth = 0
        outer = []
        for token in re.findall(r'\\[A-Za-z]+|\\.|[{}]|.', latex, re.DOTALL):
            if token == '{':
                depth += 1
            elif token == '}':
                depth -= 1
                if depth < 0:
                    flags.append('unbalanced_braces')
                    break
            elif depth == 0:
                outer.append(token)
        if depth != 0 and 'unbalanced_braces' not in flags:
            flags.append('unbalanced_braces')
        if re.search(r'\\(?:input|include|includegraphics|write|openout|openin|read|href|url|def|newcommand|catcode|csname|special|html[A-Za-z]*)(?![A-Za-z])', latex):
            flags.append('unsafe_command')
        if not flags:
            top = ''.join(outer)
            if re.search(r'[=＝<>≤≥≈]|\\(?:leq|geq|le|ge|approx|equiv)(?![A-Za-z])', top):
                category = 'relation_candidate'
            else:
                trimmed = re.sub(r'(?:\\(?:cdot|times|colon)|[\s.:;，；。])+\Z', '', latex)
                if re.search(r'\\(?:frac|sqrt|sum|int|prod|times|cdot|pm)(?![A-Za-z])|[×÷±∑]', trimmed):
                    category = 'expression_candidate'
                else:
                    category = 'fragment_candidate'
    return {'profile': 'math-review-routing-v1', 'category': category, 'flags': flags,
            'review_status': 'pending_review', 'publishable': False,
            'can_use_for_calculation': False, 'proves_transcription_correctness': False}
