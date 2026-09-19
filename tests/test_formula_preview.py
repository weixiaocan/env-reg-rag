import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests import test_corpus_update as fixture
from tests import test_document_http_api as http_fixture
from src.application.formula_preview import FormulaPreviewService
from src.application.pdf_source_audit import file_digest
from src.server.fastapi_app import create_app


class FormulaPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CorpusUpdateTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.row = self.fixture.row(name='formula.pdf', rel='data/raw/资料/formula.pdf', data=b'fixture')
        self.fixture.write_inventory([self.row])
        self.anchor = {'anchor_id': 'flow', 'file_sha256': self.row['sha256'], 'physical_page': 1,
                       'formula_number': '1', 'bbox': [10, 10, 90, 30], 'context_bbox': [10, 35, 90, 80],
                       'coordinate_system': 'page_points_top_left', 'expected_latex': 'SECRET_GOLD'}
        self.gold = self.root / 'data/registry/formula-gold.json'
        self.gold.write_text(json.dumps({'anchors': [self.anchor]}), encoding='utf-8')
        folder = self.root / 'data/model_runtime/formula_regions'
        folder.mkdir(parents=True)
        self.report = folder / 'flow-v1.json'
        self.payload = {'anchor_id': 'flow', 'file_sha256': self.row['sha256'], 'physical_page': 1,
                        'formula_number': '1', 'gold_sha256': file_digest(self.gold),
                        'processing_profile': 'targeted-formula-crop-v1:200dpi',
                        'status': 'pending_review',
                        'raw_latex': '<img src=x onerror=alert(1)>', 'private_path': 'SECRET_PATH',
                        'publishable': True, 'can_use_for_calculation': True}
        self.save()
        self.service = FormulaPreviewService(self.root)
        self.client = TestClient(create_app(query_service=http_fixture.DocumentHttpApiTest._query_service(),
                                            formula_preview=self.service))

    def save(self):
        self.report.write_text(json.dumps(self.payload), encoding='utf-8')

    def tearDown(self):
        self.fixture.tearDown()

    def test_allowlisted_preview_redacts_traces_and_never_approves(self):
        response = self.client.get('/api/v1/formulas/flow')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['publishable'])
        self.assertFalse(data['can_use_for_calculation'])
        self.assertEqual(data['status'], 'pending_review')
        self.assertTrue(data['source_url'].endswith('/content#page=1'))
        self.assertNotIn('SECRET', response.text)

    def test_missing_or_stale_report_is_source_only(self):
        self.payload['gold_sha256'] = '0'*64
        self.save()
        data = self.service.get('flow')
        self.assertEqual(data['status'], 'source_page_only')
        self.assertIsNone(data['raw_latex'])
        self.report.unlink()
        self.assertEqual(self.service.get('flow')['status'], 'source_page_only')

    def test_changed_pdf_does_not_expose_transcription(self):
        (self.root / self.row['rel_path']).write_bytes(b'changed')
        self.assertIsNone(self.service.get('flow')['raw_latex'])

    def test_unreadable_pdf_degrades_without_trace_details(self):
        with patch('src.application.formula_preview.file_digest', side_effect=PermissionError('SECRET_PATH')):
            result = self.service.get('flow')
        self.assertEqual(result['status'], 'source_page_only')
        self.assertIsNone(result['source_url'])
        self.assertNotIn('SECRET', str(result))

    def test_unknown_ids_do_not_access_arbitrary_files(self):
        self.assertEqual(self.client.get('/api/v1/formulas/unknown').status_code, 404)
        self.assertIsNone(self.service.get('../flow'))

    def test_page_and_list_are_available_and_use_safe_dom(self):
        self.assertEqual(self.client.get('/api/v1/formulas').json()['items'][0]['anchor_id'], 'flow')
        page = self.client.get('/formulas')
        self.assertEqual(page.status_code, 200)
        self.assertIn('textContent', page.text)
        self.assertNotIn('innerHTML', page.text)
        self.assertIn('不可计算', page.text)

    def test_unconfigured_api_is_explicitly_unavailable(self):
        client = TestClient(create_app(query_service=http_fixture.DocumentHttpApiTest._query_service()))
        self.assertEqual(client.get('/api/v1/formulas').status_code, 503)

    def test_failed_or_corrupt_result_never_shows_old_transcription(self):
        self.payload['status'] = 'source_page_only'
        self.save()
        self.assertIsNone(self.service.get('flow')['raw_latex'])
        self.report.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.service.get('flow')['status'], 'source_page_only')
