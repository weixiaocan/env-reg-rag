import unittest
from src.evaluation.region_detection import evaluate_formula_locations


class FormulaLocationTests(unittest.TestCase):
    def test_identity_coverage_and_context_are_separate(self):
        anchors = [{'anchor_id': 'a', 'file_sha256': 's', 'physical_page': 1, 'bbox': [0, 0, 10, 10]},
                   {'anchor_id': 'b', 'file_sha256': 's', 'physical_page': 2, 'bbox': [0, 0, 10, 10]}]
        candidates = [dict(candidate_id='wrong', sha256='other', physical_page=1, kind='formula', bbox=[0, 0, 10, 10]),
                      dict(candidate_id='part', sha256='s', physical_page=1, kind='formula', bbox=[0, 0, 2, 10]),
                      dict(candidate_id='context', sha256='s', physical_page=2, kind='formula', bbox=[0, 20, 10, 30])]
        result = evaluate_formula_locations(candidates, anchors)
        self.assertEqual(result['located_anchor_count'], 0)
        self.assertEqual(result['page_signal_anchor_count'], 2)
        self.assertEqual(result['anchors'][0]['best_anchor_coverage'], .2)

    def test_large_block_is_not_precise_detection(self):
        anchor = {'anchor_id': 'a', 'file_sha256': 's', 'physical_page': 1, 'bbox': [1, 1, 2, 2]}
        candidate = dict(candidate_id='large', sha256='s', physical_page=1, kind='formula', bbox=[0, 0, 100, 100])
        result = evaluate_formula_locations([candidate], [anchor])
        self.assertEqual(result['located_anchor_count'], 1)
        self.assertEqual(result['anchors'][0]['best_anchor_coverage'], 1)
        self.assertAlmostEqual(result['anchors'][0]['best_iou'], .0001)
        self.assertFalse(result['full_corpus_quality_established'])

    def test_unknown_box_and_caption_do_not_locate_formula(self):
        anchor = {'anchor_id': 'a', 'file_sha256': 's', 'physical_page': 1, 'bbox': [0, 0, 10, 10]}
        candidates = [dict(candidate_id='unknown', sha256='s', physical_page=1, kind='formula', bbox=None),
                      dict(candidate_id='caption', sha256='s', physical_page=1, kind='table_caption', bbox=[0, 0, 10, 10])]
        result = evaluate_formula_locations(candidates, [anchor])
        self.assertEqual(result['located_anchor_count'], 0)

    def test_invalid_and_duplicate_anchors_fail(self):
        anchor = {'anchor_id': 'a', 'file_sha256': 's', 'physical_page': 1, 'bbox': [0, 0, 10, 10]}
        with self.assertRaises(ValueError):
            evaluate_formula_locations([], [anchor, anchor])
        anchor['bbox'] = [0, 0, float('nan'), 10]
        with self.assertRaises(ValueError):
            evaluate_formula_locations([], [anchor])

    def test_empty_set_is_not_claimed_as_perfect(self):
        result = evaluate_formula_locations([], [])
        self.assertEqual(result['anchor_count'], 0)
        self.assertFalse(result['full_corpus_quality_established'])
