import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from src.evaluation.formula_triage import classify_math_region
from scripts.triage_corpus_formulas import audit_checkpoints


class FormulaTriageTests(unittest.TestCase):
    def test_checkpoint_identity_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            sha = 'a' * 64
            provenance = {'profile': 'layout-formula-v1:200dpi:score0.5:padding2point',
                          'model_checksums': {}, 'package_versions': {}}
            config = hashlib.sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()
            result = {'sha256': sha, 'profile': 'layout-formula-v1:200dpi:score0.5',
                      'coordinate_system': 'page_points_top_left', 'model_checksums': {},
                      'package_versions': {}, 'pages': [{'physical_page': 1, 'regions': []}]}
            record = {'sha256': sha, 'physical_page': 1, 'config_hash': config,
                      'status': 'completed', 'result': result}
            target = path / f'{sha}-1.json'
            target.write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(audit_checkpoints(path, [(sha, 1)], config)['page_count'], 1)
            result['package_versions'] = {'paddleocr': 'changed'}
            target.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'provenance'):
                audit_checkpoints(path, [(sha, 1)], config)
            record['physical_page'] = 2
            target.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'identity'):
                audit_checkpoints(path, [(sha, 1)], config)

    def test_target_formula_candidates(self):
        for text in [r'Q=\frac{1}{n}\sum_{i=1}^{n}A\times L_i/\Delta t_i',
                     r'A(矩形)=管沟宽\times水位高', r'A(圆形)=\frac12lR\pm\frac12dh']:
            result = classify_math_region(text)
            self.assertEqual(result['category'], 'relation_candidate')
            self.assertFalse(result['publishable'])
            self.assertFalse(result['can_use_for_calculation'])

    def test_fragments_and_non_equation_expression(self):
        for text in [r'(\mathrm{m}^3/\mathrm{d})', r'\Delta t_{i}.', r'L_{i}1', r'd\cdot']:
            self.assertEqual(classify_math_region(text)['category'], 'fragment_candidate')
        self.assertEqual(classify_math_region(r'\frac{A}{B}')['category'], 'expression_candidate')

    def test_invalid_and_unsafe_transcription(self):
        for text in [None, '', r'Q=\frac{A}{B', r'\input{secret}', r'\write18{command}', 'x' * 10001]:
            self.assertEqual(classify_math_region(text)['category'], 'needs_review')

    def test_relation_inside_sum_is_not_outer_equation(self):
        self.assertEqual(classify_math_region(r'\sum_{i=1}^{n}A_i')['category'], 'expression_candidate')

    def test_escaped_braces_do_not_break_balance(self):
        self.assertNotIn('unbalanced_braces', classify_math_region(r'\{x\}')['flags'])
