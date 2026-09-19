"""Literal native PDF text for orientation, never verified formula semantics."""


def source_context(pdf, physical_page, bbox, document_id):
    if type(physical_page) is not int or not 1 <= physical_page <= len(pdf):
        raise ValueError('invalid source page')
    result = []
    for number in range(max(1, physical_page - 1), min(len(pdf), physical_page + 1) + 1):
        page = pdf[number - 1]
        # Previous page bottom / next page top are navigation aids, not associations.
        if number < physical_page:
            top, bottom = max(0, page.rect.height - 240), page.rect.height
        elif number > physical_page:
            top, bottom = 0, min(page.rect.height, 240)
        else:
            top, bottom = max(0, bbox[1] - 100), min(page.rect.height, bbox[3] + 240)
        excerpts = []
        for block in page.get_text('blocks', sort=True):
            if len(block) < 7 or block[6] != 0 or block[3] < top or block[1] > bottom:
                continue
            text = block[4].strip()
            if text and len(excerpts) < 40:
                excerpts.append({'text': text[:4000], 'physical_page': number,
                                 'bbox': [float(v) for v in block[:4]],
                                 'verified': False})
        result.append({'physical_page': number, 'relation': 'current' if number == physical_page else 'previous' if number < physical_page else 'next',
                       'source_url': f'/api/v1/documents/{document_id}/content#page={number}',
                       'status': 'unverified_native_text' if excerpts else 'no_native_text_in_window',
                       'verified': False, 'excerpts': excerpts})
    return result
