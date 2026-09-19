import hashlib
import json
import unittest
from unittest.mock import patch
from tests import test_corpus_update as fixture
from src.application.corpus_update import CorpusUpdateService


class FullFormulaGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.CorpusUpdateTest(); self.fixture.setUp()
        self.root = self.fixture.root
        self.row = self.fixture.row(name='formula.pdf', rel='data/raw/资料/formula.pdf', data=b'fixture')
        self.fixture.write_inventory([self.row])
        self.folder = self.root/'data/model_runtime/corpus_formulas/0123456789abcdef'
        self.folder.mkdir(parents=True)
        self.report = {'schema_version': '1', 'config_hash': '0123456789abcdef'+'0'*48,
                       'document_count': 1, 'page_count': 1, 'region_count': 1,
                       'review_status': 'pending_review', 'checkpoint_manifest_sha256': 'a'*64,
                       'regions': [{'candidate_id': 'layout-'+self.row['sha256'][:16]+'-1-0',
                                    'sha256': self.row['sha256'], 'physical_page': 1,
                                    'bbox': [10, 10, 50, 30], 'raw_latex': 'Q=A',
                                    'publishable': True, 'can_use_for_calculation': True}]}
        self.save()
        self.auditor=patch('src.application.formula_region_gate.audit_checkpoints', side_effect=lambda *args: self.report)
        self.auditor.start()

    def save(self):
        (self.folder/'triage.json').write_text(json.dumps(self.report), encoding='utf-8')

    def tearDown(self):
        self.auditor.stop()
        self.fixture.tearDown()

    def test_full_layout_regions_are_bound_to_candidate_and_never_approved(self):
        parser = fixture.FakePageExtractor()
        base = CorpusUpdateService(self.root, page_extractor=parser).build()
        candidate = CorpusUpdateService(self.root, page_extractor=parser,
                         formula_run_id='0123456789abcdef').build()
        self.assertNotEqual(base['corpus_version'], candidate['corpus_version'])
        self.assertEqual(len(parser.calls), 1)
        self.assertEqual(candidate['formula_region_gate']['region_count'], 1)
        units = [json.loads(line) for line in (self.root/candidate['evidence_units']).read_text(encoding='utf-8').split('\n') if line]
        self.assertTrue(units)
        self.assertTrue(all(u['usage_policy']=='source_locator_only' for u in units))
        self.assertFalse((self.root/'data/registry/corpus-current.json').exists())

    def test_wrong_source_page_coverage_duplicate_and_running_batch_rejected(self):
        service = CorpusUpdateService(self.root, page_extractor=fixture.FakePageExtractor(), formula_run_id='0123456789abcdef')
        for field,value in [('document_count',2), ('page_count',2), ('config_hash','0'*64)]:
            before=self.report[field]; self.report[field]=value; self.save()
            with self.assertRaises(ValueError): service.build()
            self.report[field]=before
        region=self.report['regions'][0]; original=region['sha256']; region['sha256']='0'*64; self.save()
        with self.assertRaises(ValueError): service.build()
        region['sha256']=original
        self.report['regions']*=2; self.report['region_count']=2; self.save()
        with self.assertRaises(ValueError): service.build()
        (self.folder/'run.lock').write_text('busy')
        with self.assertRaises(ValueError): service.build()

    def test_report_byte_changes_change_candidate_without_reparsing(self):
        parser=fixture.FakePageExtractor()
        service=CorpusUpdateService(self.root, page_extractor=parser, formula_run_id='0123456789abcdef')
        first=service.build()
        self.report['regions'][0]['raw_latex']='Q=Changed'; self.save()
        second=service.build()
        self.assertNotEqual(first['corpus_version'],second['corpus_version'])
        self.assertEqual(len(parser.calls),1)

    def test_stale_checkpoints_rejected(self):
        service=CorpusUpdateService(self.root, page_extractor=fixture.FakePageExtractor(), formula_run_id='0123456789abcdef')
        stale=dict(self.report,checkpoint_manifest_sha256='0'*64)
        with patch('src.application.formula_region_gate.audit_checkpoints',return_value=stale):
            with self.assertRaisesRegex(ValueError,'stale'): service.build()
