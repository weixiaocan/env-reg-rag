import json
from pathlib import Path
import unittest
from src.application.formula_context import attach_reviewed_context


class CircleContextReviewTests(unittest.TestCase):
    def test_four_source_definitions_do_not_approve_sign_or_calculation(self):
        root = Path(__file__).resolve().parents[1]
        records = json.loads((root / 'data/registry/formula-context-reviews.json').read_text(encoding='utf-8'))['records']
        result = {'anchor_id': 'cecs758-area-circular-2',
                  'file_sha256': 'f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941',
                  'physical_page': 68, 'formula_number': '条文说明局部公式(2)',
                  'processing_profile': 'targeted-formula-crop-v1:200dpi',
                  'raw_latex': r'A\left( 圆形 \right)=\frac{1}{2}l R\pm\frac{1}{2}d h',
                  'status': 'pending_review', 'context_evidence': {'bbox': [60, 295, 300, 455]}}
        attach_reviewed_context(result, records)
        self.assertEqual([v['symbol'] for v in result['variable_definitions']], ['l', 'R', 'd', 'h'])
        self.assertTrue(all(v['unit'] == 'm' for v in result['variable_definitions']))
        self.assertEqual(result['context_review']['missing_symbols'], ['A'])
        self.assertIn('verified_conditions', result['missing_context'])
        self.assertIn('verified_variables', result['missing_context'])
        self.assertFalse(result['can_use_for_calculation'])
        self.assertFalse(result['publishable'])
