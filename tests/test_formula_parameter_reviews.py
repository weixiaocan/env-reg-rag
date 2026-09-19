import copy
import hashlib
import unittest
from src.application.formula_parameter_reviews import attach_parameter_review


class ParameterReviewTests(unittest.TestCase):
    def setUp(self):
        self.item = {'candidate_id': 'region', 'file_sha256': 'a'*64,
                     'physical_page': 2, 'bbox': [1, 2, 30, 40], 'raw_latex': 'V=Q'}
        self.record = {**self.item, 'transcription_sha256': hashlib.sha256(b'V=Q').hexdigest(),
            'status': 'source_visual_checked', 'required_symbols': ['Q'], 'variables': [
            {'symbol': 'Q', 'definition': '流量', 'unit': 'm³/d', 'source_text': 'Q——流量（m³/d）',
             'physical_page': 3, 'bbox': [1, 2, 30, 40], 'private_path': 'SECRET'}], 'conditions': []}
        self.report = {'schema_version': '1', 'triage_sha256': 'b'*64, 'records': [self.record]}

    def test_cross_page_review_has_exact_provenance_and_safe_fields(self):
        self.assertTrue(attach_parameter_review(self.item, self.report, 'b'*64, 3))
        self.assertEqual(self.item['reviewed_parameters'][0]['physical_page'], 3)
        self.assertNotIn('private_path', self.item['reviewed_parameters'][0])
        self.assertEqual(self.item['parameter_missing_symbols'], [])

    def test_stale_identity_duplicate_and_wrong_page_are_rejected(self):
        for key, value in [('bbox', [2, 2, 30, 40]), ('transcription_sha256', '0'*64), ('status', 'approved')]:
            report = copy.deepcopy(self.report); report['records'][0][key] = value
            self.assertFalse(attach_parameter_review(self.item, report, 'b'*64, 3))
        self.assertFalse(attach_parameter_review(self.item, self.report, 'c'*64, 3))
        self.report['records'][0]['variables'][0]['physical_page'] = 5
        self.assertFalse(attach_parameter_review(self.item, self.report, 'b'*64, 5))
        self.report['records'] *= 2
        self.assertFalse(attach_parameter_review(self.item, self.report, 'b'*64, 5))
