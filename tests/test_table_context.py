import unittest
from src.ingestion.table_context import enrich_table_context


class TableContextTests(unittest.TestCase):
    def page(self):
        return {'physical_page': 3, 'width': 200, 'height': 300, 'elements': [
            {'element_id': 'caption', 'type': 'text', 'text': '表2 水质指标', 'bbox': [20, 60, 180, 75]},
            {'element_id': 'note', 'type': 'text', 'text': '注：浓度单位为mg/L。', 'bbox': [20, 205, 180, 220]},
            {'element_id': 'far', 'type': 'text', 'text': '注：其他表格', 'bbox': [20, 270, 180, 285]}]}

    def table(self):
        return {'element_id': 't', 'physical_page': 3, 'bbox': [20, 80, 180, 200],
                'cells': [{'row': 1, 'col': 1, 'text': '≥8.0mg/L', 'bbox': [80, 100, 180, 120]}],
                'cell_box_alignment': 'model_order_pending_review', 'review_reasons': []}

    def test_candidates_keep_location_and_do_not_approve(self):
        table = self.table()
        enrich_table_context(self.page(), [table])
        self.assertEqual(table['caption_candidates'][0]['source_element_id'], 'caption')
        self.assertEqual([n['source_element_id'] for n in table['footnotes']], ['note'])
        self.assertEqual(table['unit_context'][0]['text'], '≥8.0mg/L')
        self.assertEqual(table['unit_context'][0]['source_cell'], {'row': 1, 'col': 1})
        self.assertEqual(table['unit_context'][0]['physical_page'], 3)
        self.assertFalse(table['region_quality']['can_use_transcription'])
        self.assertFalse(table['publishable'])
        self.assertIn('unit_context', table['missing_context'])

    def test_competing_tables_do_not_claim_exclusive_note(self):
        a, b = self.table(), self.table()
        b['element_id'] = 'other'
        enrich_table_context(self.page(), [a, b])
        self.assertEqual(a['footnotes'][0]['association_status'], 'ambiguous')
        self.assertEqual(a['footnotes'][0]['candidate_table_ids'], ['t', 'other'])

    def test_invalid_coordinates_are_not_used(self):
        page, table = self.page(), self.table()
        page['elements'][0]['bbox'] = [20, float('nan'), 180, 75]
        table['bbox'] = [20, 80, float('inf'), 200]
        enrich_table_context(page, [table])
        self.assertEqual(table['caption_candidates'], [])
        self.assertEqual(table['footnotes'], [])
        self.assertEqual(table['region_quality']['status'], 'source_page_only')

    def test_no_units_are_invented_for_bare_numbers(self):
        table = self.table()
        table['cells'][0]['text'] = '8.0'
        page = self.page()
        page['elements'] = []
        enrich_table_context(page, [table])
        self.assertEqual(table['unit_context'], [])
        self.assertIn('units_not_found', table['region_quality']['reasons'])

    def test_no_table_structure_remains_source_only(self):
        table = self.table()
        table['cells'] = []
        enrich_table_context(self.page(), [table])
        self.assertEqual(table['region_quality']['status'], 'source_page_only')
        self.assertTrue(table['region_quality']['can_link_source_page'])

    def test_page_body_and_quality_are_not_changed(self):
        page = self.page()
        page.update(text='正常正文', quality={'status': 'pass'})
        enrich_table_context(page, [self.table()])
        self.assertEqual(page['text'], '正常正文')
        self.assertEqual(page['quality'], {'status': 'pass'})

    def test_neighboring_plain_text_is_not_made_into_a_note(self):
        page, table = self.page(), self.table()
        page['elements'] = [{'element_id': 'plain', 'text': '其他条款应符合规定',
                             'bbox': [20, 205, 180, 220]}]
        enrich_table_context(page, [table])
        self.assertEqual(table['footnotes'], [])

    def test_reprocessing_does_not_accumulate_context(self):
        page, table = self.page(), self.table()
        enrich_table_context(page, [table])
        first = repr(table)
        enrich_table_context(page, [table])
        self.assertEqual(repr(table), first)
