import unittest
from src.ingestion.table_regions import (parse_table_cells, normalize_tables,
                                         recover_tables_from_ocr_elements)


class PdfTableRegionsTests(unittest.TestCase):
    def test_cropped_table_coordinates_still_locate_original_page(self):
        page = {'width': 300, 'height': 400, 'physical_page': 2,
                'raw': {'render_width': 200, 'render_height': 100, 'render_page_width': 100,
                        'render_page_height': 50, 'render_origin': [40, 180]},
                'tables': [{'html': '<tr><th colspan="2">m</th></tr><tr><td>8.0</td><td>1.0</td></tr>',
                            'raw': {'bbox': [0, 0, 200, 100]}}]}
        table = normalize_tables(page)[0]
        self.assertEqual(table['bbox'], [40, 180, 140, 230])
        self.assertEqual(table['header_rows'], [0])
        self.assertEqual(table['cells'][0]['cell_role'], 'header')

    def test_rowspan_reserves_next_row_column(self):
        cells = parse_table_cells('<table><tr><th rowspan="2">指标</th><th>12h</th></tr><tr><td>8.0</td></tr></table>')
        self.assertEqual([(c['row'], c['col']) for c in cells], [(0, 0), (0, 1), (1, 1)])

    def test_colspan_and_nested_text(self):
        cells = parse_table_cells('<tr><th colspan="2">单位<br>mg/L</th><td><b>8.0</b></td></tr>')
        self.assertEqual(cells[0]['text'], '单位 mg/L')
        self.assertEqual(cells[1]['col'], 2)
        self.assertEqual(cells[1]['text'], '8.0')

    def test_literal_inequalities_do_not_become_html_tags(self):
        cells = parse_table_cells('<table><tr><td rowspan="2">区</td><td>4<RSI≤8</td></tr>'
                                  '<tr><td>12<RPI<=24</td></tr></table>')
        self.assertEqual([(c['row'], c['col']) for c in cells], [(0, 0), (0, 1), (1, 1)])
        self.assertEqual([c['text'] for c in cells], ['区', '4<RSI≤8', '12<RPI<=24'])

    def test_bad_span_fails_closed(self):
        for span in ['0', '-1', 'abc', '9999999']:
            with self.assertRaises(ValueError):
                parse_table_cells('<tr><td rowspan="'+span+'">8.0</td></tr>')

    def test_normalized_region_is_not_approved(self):
        page = {'width': 100, 'height': 200, 'physical_page': 3,
                'raw': {'render_width': 200, 'render_height': 400},
                'tables': [{'table_id': 't1', 'html': '<tr><td>≥8.0mg/L</td></tr>',
                            'raw': {'bbox': [20, 40, 180, 200]}}]}
        tables = normalize_tables(page)
        self.assertEqual(tables[0]['bbox'], [10, 20, 90, 100])
        self.assertEqual(tables[0]['review_status'], 'pending_review')
        self.assertFalse(tables[0]['publishable'])
        self.assertEqual(tables[0]['cells'][0]['text'], '≥8.0mg/L')

    def test_missing_structure_is_not_flattened_into_a_cell(self):
        page = {'physical_page': 1, 'tables': [{'html': '', 'text': '磷酸盐8.0mg/L', 'raw': {}}]}
        tables = normalize_tables(page)
        self.assertEqual(tables[0]['cells'], [])
        self.assertEqual(tables[0]['review_status'], 'source_page_only')

    def test_cell_box_union_has_explicit_provenance(self):
        page = {'width': 100, 'height': 100, 'physical_page': 1,
                'raw': {'render_width': 200, 'render_height': 200},
                'tables': [{'html': '<tr><td>8.0</td></tr>',
                            'raw': {'cell_box_list': [[20, 40, 100, 80]]}}]}
        table = normalize_tables(page)[0]
        self.assertEqual(table['cells'][0]['bbox'], [10, 20, 50, 40])
        self.assertEqual(table['bbox_basis'], 'union_of_model_cell_boxes')
        self.assertFalse(table['publishable'])
        page['tables'][0]['raw']['cell_box_list'].append([100, 40, 180, 80])
        table = normalize_tables(page)[0]
        self.assertIsNone(table['cells'][0]['bbox'])
        self.assertEqual(table['cell_box_alignment'], 'not_established')

    def test_ocr_geometry_fallback_keeps_literal_boxes_without_approving_structure(self):
        page = {'width': 300, 'height': 400, 'physical_page': 7,
                'elements': [
                    {'element_id': 'a', 'text': '项目', 'bbox': [20, 100, 55, 112]},
                    {'element_id': 'b', 'text': '允许偏差', 'bbox': [120, 100, 180, 112]},
                    {'element_id': 'c', 'text': '管径', 'bbox': [20, 130, 55, 142]},
                    {'element_id': 'd', 'text': '±10 mm', 'bbox': [120, 130, 175, 142]},
                    {'element_id': 'outside', 'text': '正文', 'bbox': [20, 20, 55, 32]},
                ]}
        table = recover_tables_from_ocr_elements(page, [[10, 90, 200, 160]])[0]
        self.assertEqual([(cell['row'], cell['col']) for cell in table['cells']],
                         [(0, 0), (0, 1), (1, 0), (1, 1)])
        self.assertEqual([cell['text'] for cell in table['cells']],
                         ['项目', '允许偏差', '管径', '±10 mm'])
        self.assertEqual(table['bbox_basis'], 'known_source_table_crop')
        self.assertIn('merged_cells_not_established', table['region_quality']['reasons'])
        self.assertFalse(table['region_quality']['can_use_transcription'])
        self.assertFalse(table['publishable'])


if __name__ == '__main__':
    unittest.main()
