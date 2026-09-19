import copy
import unittest
import json
from tests import test_corpus_update as corpus_fixture
from src.application.corpus_update import CorpusUpdateService
from src.evaluation.evidence_builder import gated_page_units


class RegionEvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'asset_id': 'asset_test', 'document_version_id': 'doc_test', 'file_name': 'test.pdf',
                    'source_uri': '', 'processing_run_id': 'run_test'}
        self.page = {'sample_id': 's', 'physical_page': 1, 'page_index': 0,
                     'decision_status': 'approved', 'publishable': True,
                     'elements': [self.element('body', '独立正常正文', [10, 10, 90, 20], 0),
                                  self.element('formula', 'Q=错漏公式', [10, 40, 90, 60], 1)],
                     'tables': [], 'text': '独立正常正文\nQ=错漏公式'}

    def element(self, eid, text, bbox, order):
        return {'element_id': eid, 'type': 'text', 'text': text, 'bbox': bbox, 'reading_order': order}

    def run_page(self, regions=None):
        return gated_page_units(self.doc, self.page, {}, registered_regions=regions or [])

    def test_registered_region_is_locator_while_independent_body_can_answer(self):
        units = self.run_page([{'element_id': 'registered', 'bbox': [10, 40, 90, 60]}])
        answers = [u for u in units if u['usage_policy'] == 'answer_and_citation']
        locators = [u for u in units if u['usage_policy'] == 'source_locator_only']
        self.assertEqual([u['text'] for u in answers], ['独立正常正文'])
        self.assertEqual(len(locators), 1)
        self.assertIn('错漏公式', locators[0]['text'])

    def test_typed_formula_is_never_approved_by_page_or_region_boolean(self):
        self.page['elements'][1].update(type='formula', publishable=True, review_status='approved')
        units = self.run_page()
        self.assertFalse(any('错漏公式' in u['text'] for u in units if u['usage_policy']=='answer_and_citation'))

    def test_marked_table_cannot_leak_through_body_or_table_path(self):
        self.page['tables'] = [{'element_id': 't', 'bbox': [10, 40, 90, 60], 'review_status': 'pending_review',
                                'cells': [{'row': 0, 'col': 0, 'text': '8.0mg/L'}]}]
        units = self.run_page()
        self.assertFalse(any(u['evidence_type']=='table' for u in units))
        self.assertFalse(any('错漏公式' in u['text'] for u in units if u['usage_policy']=='answer_and_citation'))

    def test_missing_region_coordinates_fails_closed_without_claiming_page_failure(self):
        before = copy.deepcopy(self.page)
        units = self.run_page([{'element_id': 'unknown', 'bbox': None}])
        self.assertTrue(all(u['usage_policy']=='source_locator_only' for u in units))
        self.assertEqual(self.page, before)

    def test_missing_text_layer_still_preserves_region_locator(self):
        self.page['elements'] = []
        units = self.run_page([{'element_id': 'scan-region', 'bbox': [10, 40, 90, 60]}])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['locator']['bboxes'], [[10, 40, 90, 60]])

    def test_unmarked_table_overlapping_known_region_cannot_bypass_gate(self):
        self.page['tables'] = [{'element_id': 'legacy-table', 'bbox': [10, 40, 90, 60],
                                'cells': [{'row': 0, 'col': 0, 'text': '8.0mg/L'}]}]
        units = self.run_page([{'element_id': 'registered', 'bbox': [10, 40, 90, 60]}])
        self.assertFalse(any(u['evidence_type']=='table' for u in units))

    def test_next_candidate_binds_region_registry_and_preserves_locator_policy(self):
        fixture = corpus_fixture.CorpusUpdateTest()
        fixture.setUp()
        try:
            row = fixture.row(name='region.pdf', rel='data/raw/资料/region.pdf', data=b'fixture')
            fixture.write_inventory([row])
            parser = corpus_fixture.FakePageExtractor()
            service = CorpusUpdateService(fixture.root, page_extractor=parser)
            first = service.build()
            (fixture.root / 'data/registry/formula-gold.json').write_text(json.dumps({'anchors': [
                {'anchor_id': 'test-region', 'file_sha256': row['sha256'], 'physical_page': 1,
                 'bbox': [10, 10, 50, 30]}]}), encoding='utf-8')
            second = service.build()
            self.assertNotEqual(first['corpus_version'], second['corpus_version'])
            self.assertEqual(len(parser.calls), 1)
            path = fixture.root / 'data/retrieval' / (second['corpus_version']+'-chunks.jsonl')
            chunks = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertTrue(chunks)
            self.assertTrue(all(c['metadata']['usage_policy']=='source_locator_only' for c in chunks))
            self.assertFalse((fixture.root / 'data/registry/corpus-current.json').exists())
        finally:
            fixture.tearDown()
