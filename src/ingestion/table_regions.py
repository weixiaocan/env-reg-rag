"""Targeted table structure normalization without automatic transcription approval."""
from html.parser import HTMLParser
import math
import re
import statistics
from src.ingestion.table_context import enrich_table_context


class _Cells(HTMLParser):
    def __init__(self):
        super().__init__()
        self.row = -1
        self.col = 0
        self.occupied = set()
        self.current = None
        self.cells = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            if self.current is not None:
                raise ValueError('unclosed table cell')
            self.row += 1
            self.col = 0
        elif tag in {'td', 'th'}:
            if self.row < 0 or self.current is not None:
                raise ValueError('invalid table row/cell nesting')
            values = dict(attrs)
            rs, cs = (int(values.get(k) or 1) for k in ('rowspan', 'colspan'))
            if not 1 <= rs <= 100 or not 1 <= cs <= 100:
                raise ValueError('invalid table span')
            while (self.row, self.col) in self.occupied:
                self.col += 1
            coords = {(r, c) for r in range(self.row, self.row + rs)
                      for c in range(self.col, self.col + cs)}
            if coords & self.occupied or len(self.occupied | coords) > 10000:
                raise ValueError('overlapping or oversized table')
            self.occupied.update(coords)
            self.current = {'row': self.row, 'col': self.col,
                            'row_span': rs, 'col_span': cs, 'parts': [], 'bbox': None,
                            'cell_role': 'header' if tag == 'th' else 'data'}
        elif tag == 'br' and self.current is not None:
            self.current['parts'].append(' ')

    def handle_data(self, data):
        if self.current is not None:
            self.current['parts'].append(data)

    def handle_endtag(self, tag):
        if tag in {'td', 'th'}:
            if self.current is None:
                raise ValueError('unexpected table cell close')
            cell = self.current
            cell['text'] = re.sub(r'\s+', ' ', ''.join(cell.pop('parts'))).strip()
            self.cells.append(cell)
            self.col += cell['col_span']
            self.current = None


def parse_table_cells(html):
    if not isinstance(html, str) or len(html) > 1000000:
        raise ValueError('invalid table HTML')
    # Model HTML can contain literal inequalities such as 4<RSI<=8. Escape
    # non-table markup before HTMLParser, preserving the original operator.
    html = re.sub(r'<(?!/?(?:html|body|table|thead|tbody|tfoot|tr|td|th|br|sup|sub|b|strong|i|em|span|font|u)\b[^<>]*>)',
                  '&lt;', html, flags=re.IGNORECASE)
    parser = _Cells()
    parser.feed(html)
    parser.close()
    if parser.current is not None:
        raise ValueError('unclosed table cell')
    return parser.cells


def _bbox(page, raw):
    box = raw.get('bbox')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        width, height = page['raw']['render_width'], page['raw']['render_height']
        pw = page['raw'].get('render_page_width', page['width'])
        ph = page['raw'].get('render_page_height', page['height'])
        ox, oy = page['raw'].get('render_origin', [0, 0])
        values = [float(box[0])*pw/width + ox, float(box[1])*ph/height + oy,
                  float(box[2])*pw/width + ox, float(box[3])*ph/height + oy]
        if (not all(math.isfinite(v) for v in values)
                or not 0 <= values[0] < values[2] <= page['width']
                or not 0 <= values[1] < values[3] <= page['height']):
            return None
        return values
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _valid_page_box(page, box):
    try:
        return (isinstance(box, (list, tuple)) and len(box) == 4
                and all(math.isfinite(float(value)) for value in box)
                and 0 <= float(box[0]) < float(box[2]) <= float(page['width'])
                and 0 <= float(box[1]) < float(box[3]) <= float(page['height']))
    except (KeyError, TypeError, ValueError):
        return False


def normalize_tables(page):
    tables = []
    for index, table in enumerate(page.get('tables', [])):
        html = table.get('html') or ''
        reasons = []
        try:
            cells = parse_table_cells(html)
        except ValueError:
            cells = []
            reasons.append('invalid_table_structure')
        bbox = _bbox(page, table.get('raw') or {})
        boxes = (table.get('raw') or {}).get('cell_box_list', [])
        cell_boxes = [_bbox(page, {'bbox': box}) for box in boxes] if isinstance(boxes, list) else []
        aligned = bool(cells) and len(cell_boxes) == len(cells) and all(cell_boxes)
        if aligned:
            for cell, cell_box in zip(cells, cell_boxes, strict=True):
                cell['bbox'] = cell_box
        else:
            reasons.append('cell_coordinates_not_aligned')
        bbox_basis = 'model_table_bbox' if bbox is not None else 'missing'
        if bbox is None and aligned:
            bbox = [min(b[0] for b in cell_boxes), min(b[1] for b in cell_boxes),
                    max(b[2] for b in cell_boxes), max(b[3] for b in cell_boxes)]
            bbox_basis = 'union_of_model_cell_boxes'
        if bbox is None:
            reasons.append('missing_table_region_coordinates')
        if not cells:
            reasons.append('missing_table_cells')
        tables.append({'element_id': table.get('table_id') or f'table-{index}',
                       'physical_page': page['physical_page'], 'bbox': bbox,
                       'coordinate_system': 'page_points_top_left',
                       'bbox_basis': bbox_basis,
                       'cell_box_alignment': 'model_order_pending_review' if aligned else 'not_established',
                       'html': html, 'cells': cells, 'caption': '',
                       'header_rows': sorted({c['row'] for c in cells if c.get('cell_role') == 'header'}),
                       'unit_context': [], 'footnotes': [],
                       'missing_context': ['caption', 'unit_context', 'footnotes'] +
                                          ([] if aligned else ['cell_coordinates']),
                       'review_status': 'pending_review' if cells else 'source_page_only',
                       'review_reasons': reasons + ['table_transcription_requires_review'],
                       # publishable here is a review-subsystem status flag, NOT a V2
                       # contract field; V2 table elements do not carry it.
                       'publishable': False})
    return enrich_table_context(page, tables)


def recover_tables_from_ocr_elements(page, regions, *, failure_reason='local_table_model_timeout'):
    """Keep a detected table usable as a source locator after native model failure.

    OCR elements retain literal source text and original-page geometry.  Their
    vertical grouping is only a conservative row proposal: columns, headers and
    spans remain explicitly unverified and the transcription is never approved.
    """
    tables = []
    elements = [element for element in page.get('elements', [])
                if isinstance(element.get('text'), str) and element['text'].strip()
                and _valid_page_box(page, element.get('bbox'))]
    for index, region in enumerate(regions):
        if not _valid_page_box(page, region):
            raise ValueError('invalid OCR fallback table region')
        selected = []
        for element in elements:
            box = element['bbox']
            center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]:
                selected.append(element)
        if not selected:
            raise ValueError('OCR fallback found no source elements')
        heights = [element['bbox'][3] - element['bbox'][1] for element in selected]
        tolerance = max(2.0, statistics.median(heights) * .55)
        rows = []
        for element in sorted(selected, key=lambda value: (
                (value['bbox'][1] + value['bbox'][3]) / 2, value['bbox'][0])):
            center_y = (element['bbox'][1] + element['bbox'][3]) / 2
            if not rows or abs(center_y - rows[-1]['center_y']) > tolerance:
                rows.append({'center_y': center_y, 'elements': [element]})
            else:
                row = rows[-1]
                row['elements'].append(element)
                row['center_y'] = sum((item['bbox'][1] + item['bbox'][3]) / 2
                                      for item in row['elements']) / len(row['elements'])
        cells = []
        for row_index, row in enumerate(rows):
            for col_index, element in enumerate(sorted(row['elements'], key=lambda value: value['bbox'][0])):
                cells.append({'row': row_index, 'col': col_index, 'row_span': 1, 'col_span': 1,
                              'text': re.sub(r'\s+', ' ', element['text']).strip(),
                              'bbox': [float(value) for value in element['bbox']],
                              'cell_role': 'unverified'})
        tables.append({
            'element_id': f'ocr-fallback-table-{index}',
            'physical_page': page['physical_page'],
            'bbox': [float(value) for value in region],
            'coordinate_system': 'page_points_top_left',
            'bbox_basis': 'known_source_table_crop',
            'source_bounds_basis': 'validated_original_pdf_crop',
            'cell_box_alignment': 'ocr_element_geometry_pending_review',
            'html': '', 'cells': cells, 'caption': '', 'header_rows': [],
            'unit_context': [], 'footnotes': [],
            'missing_context': ['caption', 'unit_context', 'footnotes', 'verified_columns',
                                'verified_headers', 'verified_spans'],
            'review_status': 'pending_review',
            # publishable here is a review-subsystem status flag, NOT a V2 contract field.
            'publishable': False,
            'review_reasons': [failure_reason, 'ocr_geometry_fallback',
                               'row_column_structure_pending_review',
                               'merged_cells_not_established', 'header_rows_not_established',
                               'table_transcription_requires_review'],
        })
    return enrich_table_context(page, tables)
