import copy
import hashlib
import unittest
from src.application.formula_context import attach_reviewed_context


class FormulaContextTests(unittest.TestCase):
    def setUp(self):
        self.result = {'anchor_id': 'a', 'file_sha256': 'a'*64, 'physical_page': 1,
                       'formula_number': '1', 'processing_profile': 'v1', 'raw_latex': 'Q=A',
                       'status': 'pending_review', 'publishable': False, 'can_use_for_calculation': False,
                       'context_evidence': {'bbox': [10, 20, 90, 80]},
                       'missing_context': ['verified_variables', 'verified_units', 'verified_conditions']}
        self.record = {k: self.result[k] for k in ('anchor_id', 'file_sha256', 'physical_page', 'formula_number', 'processing_profile')}
        self.record.update(transcription_sha256=hashlib.sha256(b'Q=A').hexdigest(),
                           review_status='manual_visual_confirmed', source_bbox=[10, 20, 90, 80],
                           required_symbols=['Q', 'A'],
                           variables=[{'symbol': 'Q', 'definition': '流量', 'unit': 'm³/d', 'source_text': 'Q——流量(m³/d)'},
                                      {'symbol': 'A', 'definition': '面积', 'unit': 'm²', 'source_text': 'A——面积(m²)'}],
                           conditions=[{'text': '每日测量6次～8次', 'source_text': '每日测量6次～8次'}])

    def test_verified_variables_keep_evidence_but_never_authorize_calculation(self):
        attach_reviewed_context(self.result, [self.record])
        self.assertEqual(len(self.result['variable_definitions']), 2)
        self.assertEqual(self.result['variable_definitions'][0]['physical_page'], 1)
        self.assertEqual(self.result['units'][1]['unit'], 'm²')
        self.assertNotIn('verified_variables', self.result['missing_context'])
        self.assertIn('verified_conditions', self.result['missing_context'])
        self.assertFalse(self.result['publishable'])
        self.assertFalse(self.result['can_use_for_calculation'])

    def test_changed_identity_transcription_or_profile_does_not_attach(self):
        for key, value in [('file_sha256', 'b'*64), ('physical_page', 2), ('formula_number', '2'),
                           ('processing_profile', 'v2'), ('raw_latex', 'Q=2*A')]:
            result = copy.deepcopy(self.result)
            result[key] = value
            attach_reviewed_context(result, [self.record])
            self.assertEqual(result['variable_definitions'], [])

    def test_partial_variables_do_not_clear_missing(self):
        self.record['variables'].pop()
        attach_reviewed_context(self.result, [self.record])
        self.assertIn('verified_variables', self.result['missing_context'])

    def test_unreviewed_or_ambiguous_records_fail_closed(self):
        for records in [[dict(self.record, review_status='pending_review')], [self.record, self.record]]:
            result = copy.deepcopy(self.result)
            attach_reviewed_context(result, records)
            self.assertEqual(result['variable_definitions'], [])

    def test_invalid_source_region_does_not_attach(self):
        self.record['source_bbox'] = [0, 0, 999, 999]
        attach_reviewed_context(self.result, [self.record])
        self.assertEqual(self.result['variable_definitions'], [])

    def test_duplicate_symbols_are_rejected(self):
        self.record['variables'].append(self.record['variables'][0])
        attach_reviewed_context(self.result, [self.record])
        self.assertEqual(self.result['variable_definitions'], [])

    def test_missing_transcription_never_uses_review_as_model_output(self):
        self.result['raw_latex'] = None
        attach_reviewed_context(self.result, [self.record])
        self.assertEqual(self.result['variable_definitions'], [])
        self.assertIsNone(self.result['raw_latex'])

    def test_unstated_unit_remains_unknown(self):
        self.record['variables'][1].update(unit=None, unit_status='not_stated')
        attach_reviewed_context(self.result, [self.record])
        self.assertIsNone(self.result['units'][1]['unit'])
        self.assertIn('verified_units', self.result['missing_context'])

    def test_malformed_record_does_not_attach(self):
        attach_reviewed_context(self.result, [None])
        self.assertEqual(self.result['variable_definitions'], [])
