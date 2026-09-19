import unittest
import tempfile
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.evaluation.formula_quality_context import build_context
from scripts.audit_formula_context import audit


class QualityContextTests(unittest.TestCase):
    def fixture(self):
        return {'physical_page': 2, 'width': 400, 'height': 600,
                'decision_status': 'quarantined', 'extraction_route': 'full_ocr',
                'elements': [{'text': 'A: original OCR definition', 'bbox': [20, 110, 350, 125]}]}

    def test_ocr_definition_is_literal_unverified_and_quality_preserved(self):
        item = {'candidate_id': 'sample', 'sha256': 'a'*64, 'physical_page': 2,
                'bbox': [100, 80, 250, 100], 'raw_latex': 'A=xy'}
        result = build_context(item, [self.fixture()])
        context = result['source_context'][0]
        self.assertEqual(context['excerpts'][0]['text'], 'A: original OCR definition')
        self.assertEqual(context['page_quality'], 'quarantined')
        self.assertEqual(context['extraction_route'], 'full_ocr')
        self.assertFalse(context['verified'])
        self.assertFalse(result['can_use_for_calculation'])
        self.assertIn('manual_transcription_review', result['blockers'])

    def test_invalid_crop_or_missing_page_fails_not_empty_success(self):
        item = {'candidate_id': 'sample', 'sha256': 'a'*64, 'physical_page': 2,
                'bbox': [100, 80, 900, 100], 'raw_latex': 'A=xy'}
        with self.assertRaises(ValueError): build_context(item, [self.fixture()])
        item['bbox'] = [100, 80, 250, 100]
        with self.assertRaises(ValueError): build_context(item, [])

    def test_outside_text_ignored_and_next_page_not_automatically_associated(self):
        page = self.fixture(); page['elements'][0]['bbox'] = [1, 500, 100, 520]
        next_page = self.fixture(); next_page['physical_page'] = 3
        item = {'candidate_id': 'sample', 'sha256': 'a'*64, 'physical_page': 2,
                'bbox': [100, 80, 250, 100], 'raw_latex': 'A=xy'}
        result = build_context(item, [page, next_page])
        self.assertEqual(result['source_context'][0]['excerpts'], [])
        self.assertEqual(result['source_context'][1]['relation'], 'next')
        self.assertFalse(result['source_context'][1]['verified'])

    def test_audit_rejects_changed_canonical_and_stale_triage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'data/model_runtime/corpus_formulas/0123456789abcdef'
            folder.mkdir(parents=True)
            canonical = root/'data/canonical/documents.jsonl'
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b'')
            triage = {'config_hash': 'fixture', 'regions': [], 'checkpoint_manifest_sha256': 'fixture'}
            (folder/'triage.json').write_text(json.dumps(triage), encoding='utf-8')
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps({'canonical_documents': 'data/canonical/documents.jsonl',
                'canonical_documents_sha256': hashlib.sha256(b'').hexdigest()}), encoding='utf-8')
            with patch('scripts.audit_formula_context.build_source_catalog', return_value=SimpleNamespace(assets=[])), \
                 patch('scripts.audit_formula_context.audit_checkpoints', return_value=triage), \
                 patch('scripts.audit_formula_context.resolve_current_corpus', return_value=SimpleNamespace(manifest_path=manifest, corpus_version='fixture')):
                self.assertEqual(audit(root, '0123456789abcdef')['region_count'], 0)
                canonical.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'canonical checksum'):
                    audit(root, '0123456789abcdef')
                canonical.write_bytes(b'')
                stale = dict(triage, checkpoint_manifest_sha256='changed')
                with patch('scripts.audit_formula_context.audit_checkpoints', return_value=stale):
                    with self.assertRaisesRegex(ValueError, 'stale triage'):
                        audit(root, '0123456789abcdef')
