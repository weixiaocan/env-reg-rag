"""Compare every recovered cell with original source-layer text, without approval."""
import re

import pymupdf


def literal(value):
    return re.sub(r'\s+', '', value.replace('≥', '>=').replace('≤', '<='))


def cell_source_checks(pdf_page, region):
    checks = []
    for index, table in enumerate(region.get('tables', [])):
        for cell in table.get('cells', []):
            item = {'region_id': region['region_id'], 'table_index': index,
                    'row': cell['row'], 'col': cell['col'], 'bbox': cell.get('bbox'),
                    'transcribed_text': cell['text'], 'verified': False,
                    'can_approve_transcription': False}
            box = cell.get('bbox')
            if not box:
                item['status'] = 'original_cell_geometry_not_recovered'
            elif not literal(cell['text']):
                item['status'] = 'empty_cell_text_pending_original_review'
            else:
                source_box = pymupdf.Rect(box) * pdf_page.derotation_matrix
                source_text = pdf_page.get_textbox(source_box + (-1, -1, 1, 1))
                item['original_source_text'] = source_text
                item['status'] = ('original_cell_text_layer_unavailable' if not literal(source_text) else
                    'literal_cell_text_matches_original_layer_pending_review'
                    if literal(cell['text']) == literal(source_text) else
                    'cell_text_differs_from_original_text_layer')
            checks.append(item)
    return checks
