"""Deterministic, read-only discovery of structured-content block candidates.

Block boxes are not precise formula/table detection boxes. No approval is inferred.
"""
import hashlib
import math
import re


def _box(item, page):
    box = item.get('bbox')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
        return None
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 and 0 <= y0 < y1):
        return None
    if not (x1 <= page.get('width', 0) and y1 <= page.get('height', 0)):
        return None
    return list(box)


def discover_regions(documents):
    candidates = []
    page_count = empty_pages = document_count = 0
    for doc in documents:
        document_count += 1
        sha = doc['sha256']
        if not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError('invalid document SHA-256')
        for page in doc['pages']:
            page_count += 1
            elements = page.get('elements', [])
            if not any(str(e.get('text') or '').strip() for e in elements):
                empty_pages += 1
            items = [('element', i, e) for i, e in enumerate(elements)]
            items += [('table', i, t) for i, t in enumerate(page.get('tables', []))]
            for origin, index, item in items:
                text = str(item.get('text') or '')
                kind = str(item.get('type', '')).lower()
                signals = {}
                if kind in {'formula', 'equation'}:
                    signals.setdefault('formula', []).append('typed_element')
                if re.search(r'式中|按下列公式|计算公式', text):
                    signals.setdefault('formula', []).append('formula_context_text')
                if re.search(r'[=＝].*[×÷±∑/]|[×÷±∑/].*[=＝]', text):
                    signals.setdefault('formula', []).append('equality_and_math_operator')
                if origin == 'table' or kind == 'table':
                    signals.setdefault('table', []).append('structured_table')
                if re.match(r'^\s*(?:续\s*)?表\s*\d', text):
                    signals.setdefault('table_caption', []).append('caption_text_only')
                for candidate_kind, reasons in signals.items():
                    element_id = str(item.get('element_id') or item.get('table_id') or f'{origin}-{index}')
                    identity = f'{sha}:{page["physical_page"]}:{origin}:{index}:{element_id}:{candidate_kind}'
                    box = _box(item, page)
                    candidates.append({
                        'candidate_id': 'region_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        'sha256': sha, 'file_name': doc.get('file_name'),
                        'document_version_id': doc.get('document_version_id'),
                        'physical_page': page['physical_page'], 'element_id': element_id,
                        'kind': candidate_kind, 'reasons': reasons, 'bbox': box,
                        'location_status': 'block_bbox' if box else 'unknown',
                        'text_preview': text[:400], 'review_status': 'pending_review',
                        # publishable here is a discovery-candidate review flag, NOT a V2
                        # contract field; region discovery is a diagnostic path.
                        'publishable': False, 'can_use_for_calculation': False,
                    })
    return {'schema_version': '1', 'profile': 'native-block-signals-v1',
            'document_count': document_count, 'page_count': page_count,
            'pages_without_text': empty_pages, 'candidate_count': len(candidates),
            'complete_structure_coverage': False, 'candidates': candidates}
