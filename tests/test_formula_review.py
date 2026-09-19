import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests import test_document_http_api as http_fixture
from src.server.fastapi_app import create_app
from tests import test_corpus_update as fixture
from src.application.formula_review import FormulaReviewService


class FormulaReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CorpusUpdateTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.row = self.fixture.row(name='sample.pdf', rel='data/raw/资料/sample.pdf', data=b'fixture')
        self.fixture.write_inventory([self.row])
        self.folder = self.root / 'data/model_runtime/corpus_formulas/0123456789abcdef'
        self.folder.mkdir(parents=True)
        self.identity = 'layout-' + self.row['sha256'][:16] + '-1-0'
        self.payload = {'review_status': 'pending_review', 'regions': [
            {'candidate_id': self.identity, 'sha256': self.row['sha256'], 'physical_page': 1,
             'bbox': [1, 1, 20, 20], 'raw_latex': 'Q=A', 'private_path': 'SECRET',
             'publishable': True, 'can_use_for_calculation': True}]}
        self.save()
        self.service = FormulaReviewService(self.root, '0123456789abcdef')

    def save(self):
        (self.folder / 'triage.json').write_text(json.dumps(self.payload), encoding='utf-8')

    def tearDown(self):
        self.fixture.tearDown()

    def test_pagination_filter_and_safe_detail(self):
        listing = self.service.list(category='relation_candidate', offset=0, limit=10)
        self.assertEqual(listing['total'], 1)
        self.assertEqual(self.service.list(offset=1, limit=10)['items'], [])
        detail = self.service.get(self.identity)
        self.assertEqual(detail['raw_latex'], 'Q=A')
        self.assertFalse(detail['publishable'])
        self.assertNotIn('SECRET', json.dumps(detail))
        self.assertTrue(detail['source_url'].endswith('#page=1'))
        self.assertTrue(detail['image_url'].endswith('/image'))
        self.assertEqual(detail['reviewed_parameters'], [])

    def test_visual_findings_are_bound_diagnostics_not_approval(self):
        import hashlib
        path = self.root / 'data/registry/formula-visual-findings.json'
        record = {'candidate_id': self.identity, 'file_sha256': self.row['sha256'],
                  'physical_page': 1, 'bbox': [1, 1, 20, 20],
                  'transcription_sha256': hashlib.sha256(b'Q=A').hexdigest(),
                  'screen_status': 'original_crop_screened',
                  'finding': 'suspected_transcription_or_crop_issue', 'notes': 'decimal lost',
                  'publishable': True, 'can_use_for_calculation': True, 'private_path': 'SECRET'}
        report = {'schema_version': '1', 'run_id': self.folder.name,
                  'triage_sha256': hashlib.sha256((self.folder / 'triage.json').read_bytes()).hexdigest(),
                  'records': [record]}
        path.write_text(json.dumps(report), encoding='utf-8')
        detail = self.service.get(self.identity)
        self.assertEqual(detail['visual_screening']['notes'], 'decimal lost')
        self.assertFalse(detail['visual_screening']['verified'])
        self.assertFalse(detail['visual_screening']['can_use_for_calculation'])
        self.assertNotIn('SECRET', json.dumps(detail))
        for key, value in [('bbox', [2, 1, 20, 20]), ('transcription_sha256', '0'*64),
                           ('physical_page', 2), ('finding', 'approved')]:
            bad = {**record, key: value}
            report['records'] = [bad]
            path.write_text(json.dumps(report), encoding='utf-8')
            self.assertNotIn('visual_screening', self.service.get(self.identity))
        report['records'] = [record, record]
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertNotIn('visual_screening', self.service.get(self.identity))

    def test_source_bound_correction_keeps_model_output_and_stays_non_calculable(self):
        import hashlib
        path = self.root / 'data/registry/formula-corrections.json'
        record = {'candidate_id': self.identity, 'file_sha256': self.row['sha256'],
                  'physical_page': 1, 'bbox': [1, 1, 20, 20],
                  'original_transcription_sha256': hashlib.sha256(b'Q=A').hexdigest(),
                  'corrected_latex': 'Q=A\\,v', 'status': 'source_image_transcribed_pending_expert',
                  'basis': 'original_pdf_crop', 'can_use_for_calculation': True}
        report = {'schema_version': '1', 'run_id': self.folder.name,
                  'triage_sha256': hashlib.sha256((self.folder / 'triage.json').read_bytes()).hexdigest(),
                  'records': [record]}
        path.write_text(json.dumps(report), encoding='utf-8')
        detail = self.service.get(self.identity)
        self.assertEqual(detail['raw_latex'], 'Q=A')
        self.assertEqual(detail['corrected_reading']['latex'], 'Q=A\\,v')
        self.assertFalse(detail['corrected_reading']['verified_for_calculation'])
        self.assertFalse(detail['corrected_reading']['can_use_for_calculation'])
        for key, value in [('bbox', [2, 1, 20, 20]), ('corrected_latex', ''),
                           ('original_transcription_sha256', '0'*64), ('status', 'approved')]:
            report['records'] = [{**record, key: value}]
            path.write_text(json.dumps(report), encoding='utf-8')
            self.assertNotIn('corrected_reading', self.service.get(self.identity))

    def test_source_bound_non_formula_disposition_is_not_a_correction(self):
        import hashlib
        path = self.root / 'data/registry/formula-corrections.json'
        record = {'candidate_id': self.identity, 'file_sha256': self.row['sha256'],
                  'physical_page': 1, 'bbox': [1, 1, 20, 20],
                  'original_transcription_sha256': hashlib.sha256(b'Q=A').hexdigest(),
                  'status': 'source_text_not_standalone_formula', 'basis': 'original_pdf_full_width_line',
                  'publishable': False, 'can_use_for_calculation': False}
        report = {'schema_version': '1', 'run_id': self.folder.name,
                  'triage_sha256': hashlib.sha256((self.folder / 'triage.json').read_bytes()).hexdigest(),
                  'records': [], 'dispositions': [record]}
        path.write_text(json.dumps(report), encoding='utf-8')
        detail = self.service.get(self.identity)
        self.assertEqual(detail['review_disposition']['status'], 'source_text_not_standalone_formula')
        self.assertNotIn('corrected_reading', detail)
        self.assertFalse(detail['review_disposition']['can_use_for_calculation'])
        report['records'] = [record]
        report['triage_sha256'] = '0'*64
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertNotIn('visual_screening', self.service.get(self.identity))

    def test_image_reads_original_pdf_not_transcription(self):
        import pymupdf
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((50, 50), 'Q = A * v')
        data = pdf.tobytes()
        pdf.close()
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        row = self.fixture.row(name='real.pdf', rel='data/raw/资料/real.pdf', data=data)
        self.fixture.write_inventory([row])
        identity = 'layout-' + sha[:16] + '-1-0'
        self.payload['regions'][0].update(candidate_id=identity, sha256=sha, bbox=[40, 30, 160, 60], raw_latex='WRONG')
        self.save()
        service = FormulaReviewService(self.root, '0123456789abcdef')
        image = service.image(identity)
        self.assertTrue(image.startswith(b'\x89PNG'))
        detail = service.get(identity)
        self.assertIn('Q = A * v', detail['source_context'][0]['excerpts'][0]['text'])
        self.assertFalse(detail['source_context'][0]['verified'])
        self.assertTrue(service.image(identity, full_page=True).startswith(b'\x89PNG'))
        self.assertIsNone(service.image(identity, full_page=True, page_offset=-1))
        with self.assertRaises(ValueError):
            service.image(identity, full_page=True, page_offset=2)

    def test_changed_source_suppresses_transcription(self):
        (self.root / self.row['rel_path']).write_bytes(b'changed')
        detail = self.service.get(self.identity)
        self.assertIsNone(detail['raw_latex'])
        self.assertIsNone(detail['source_url'])
        self.assertEqual(detail['source_context'], [])

    def test_invalid_filter_and_unknown_id(self):
        with self.assertRaises(ValueError):
            self.service.list(category='approved')
        self.assertIsNone(self.service.get('../secret'))

    def test_parameters_require_same_transcription_and_location(self):
        gold = self.root / 'data/registry/formula-gold.json'
        gold.write_text(json.dumps({'anchors': [{'anchor_id': 'registered',
            'file_sha256': self.row['sha256'], 'physical_page': 1, 'bbox': [1, 1, 20, 20]}]}), encoding='utf-8')
        preview = {'raw_latex': 'Q=A', 'variable_definitions': [{'symbol': 'Q', 'definition': 'verified', 'unit': None}], 'conditions': []}
        with patch('src.application.formula_review.FormulaPreviewService.get', return_value=preview):
            self.assertEqual(len(self.service.get(self.identity)['reviewed_parameters']), 1)
            preview['raw_latex'] = 'different'
            self.assertEqual(self.service.get(self.identity)['reviewed_parameters'], [])

    def test_changed_source_has_no_image(self):
        (self.root / self.row['rel_path']).write_bytes(b'changed')
        self.assertIsNone(self.service.image(self.identity))

    def test_canonical_context_requires_exact_run_source_and_transcription(self):
        import hashlib
        report = {'status': 'pending_review',
                  'triage_sha256': hashlib.sha256((self.folder/'triage.json').read_bytes()).hexdigest(),
                  'records': [{'candidate_id': self.identity, 'file_sha256': self.row['sha256'],
                               'physical_page': 1, 'transcription_sha256': hashlib.sha256(b'Q=A').hexdigest(),
                               'source_context': [{'physical_page': 1, 'page_quality': 'quarantine',
                                   'extraction_route': 'full_ocr', 'verified': True,
                                   'excerpts': [{'text': 'original OCR cue', 'bbox': [1, 1, 20, 20]}]}]}]}
        path = self.folder/'quality-context.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        detail = self.service.get(self.identity)
        self.assertEqual(detail['source_context'][0]['page_quality'], 'quarantine')
        self.assertFalse(detail['source_context'][0]['verified'])
        self.assertEqual(detail['reviewed_parameters'], [])
        report['records'][0]['transcription_sha256'] = '0'*64
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.service.get(self.identity)['source_context'], [])
        report['records'][0]['transcription_sha256'] = hashlib.sha256(b'Q=A').hexdigest()
        report['triage_sha256'] = '0'*64
        path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(self.service.get(self.identity)['source_context'], [])

    def test_duplicate_identity_fails(self):
        self.payload['regions'] *= 2
        self.save()
        with self.assertRaises(ValueError):
            self.service.list()

    def test_out_of_range_page_rejected(self):
        self.payload['regions'][0].update(physical_page=999, candidate_id='layout-' + self.row['sha256'][:16] + '-999-0')
        self.save()
        with self.assertRaises(ValueError):
            self.service.list()

    def test_missing_report_http_fails_closed(self):
        (self.folder / 'triage.json').unlink()
        client = TestClient(create_app(query_service=http_fixture.DocumentHttpApiTest._query_service(), formula_review=self.service))
        self.assertEqual(client.get('/api/v1/formula-review').status_code, 503)

    def test_http_routes_and_filter_errors(self):
        client = TestClient(create_app(query_service=http_fixture.DocumentHttpApiTest._query_service(), formula_review=self.service))
        self.assertEqual(client.get('/formula-review').status_code, 200)
        self.assertEqual(client.get('/api/v1/formula-review?category=relation_candidate').json()['total'], 1)
        self.assertEqual(client.get('/api/v1/formula-review?limit=999').status_code, 400)
        self.assertEqual(client.get('/api/v1/formula-review/not-found').status_code, 404)
        self.assertEqual(client.get('/api/v1/formula-review/' + self.identity + '/image?full_page=true&page_offset=2').status_code, 400)
        self.assertEqual(client.get('/api/v1/formula-review/' + self.identity + '/image?page_offset=1').status_code, 400)
        self.assertFalse(client.get('/api/v1/formula-review/' + self.identity).json()['publishable'])
