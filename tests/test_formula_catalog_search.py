from unittest.mock import patch
from tests.test_formula_review import FormulaReviewTests


class FormulaCatalogSearchTests(FormulaReviewTests):
    def test_stale_context_is_not_searchable(self):
        import json
        (self.folder / 'quality-context.json').write_text(json.dumps({
            'status': 'pending_review', 'triage_sha256': '0'*64,
            'records': [{'candidate_id': self.identity, 'source_context': [
                {'excerpts': [{'text': '不应检索的旧说明'}]}]}]}), encoding='utf-8')
        self.assertEqual(self.service.list(query='不应检索')['total'], 0)
        self.assertEqual(self.service.list(query='sample')['total'], 1)

    def test_detail_exposes_literal_parameter_text_not_verified_table(self):
        def context(item, **kwargs):
            item['source_context'] = [{'physical_page': 1, 'relation': 'current',
                'excerpts': [{'text': '式中：Q——流量（m³/d）', 'bbox': [1, 21, 100, 40]}]}]
        with patch.object(self.service, '_canonical_context', side_effect=context):
            detail = self.service.get(self.identity)
        reading = detail['parameter_reading']
        self.assertEqual(reading['excerpts'][0]['text'], '式中：Q——流量（m³/d）')
        self.assertFalse(reading['excerpts'][0]['verified'])
        self.assertEqual(detail['reviewed_parameters'], [])

    def test_search_uses_bound_context_and_paginates_matches(self):
        def context(item, **kwargs):
            item['source_context'] = [{'physical_page': 1, 'relation': 'current',
                'excerpts': [{'text': '缺氧区容积，式中：Vn——容积（m³）',
                              'bbox': [1, 21, 100, 40]}]}]
        (self.folder / 'quality-context.json').write_text('{}', encoding='utf-8')
        with patch.object(self.service, '_canonical_context', side_effect=context):
            self.assertEqual(self.service.list(query='缺氧 容积')['total'], 1)
            self.assertEqual(self.service.list(query='缺氧 容积', offset=1)['items'], [])
            self.assertEqual(self.service.list(query='沉淀')['total'], 0)
        with self.assertRaises(ValueError):
            self.service.list(query='x'*201)
