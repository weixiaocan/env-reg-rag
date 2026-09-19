import unittest
import tempfile
from src.ingestion.formula_layout import CachedLayoutEngine, formula_regions


class FormulaLayoutTests(unittest.TestCase):
    def test_real_backend_numpy_coordinates(self):
        import numpy as np
        boxes = [{'label': 'formula', 'score': .9,
                  'coordinate': list(np.array([20, 40, 200, 100], dtype=np.float32))}]
        self.assertEqual(len(formula_regions(boxes, 400, 600, 200, 300)), 1)

    def test_pixel_to_point_and_formula_only(self):
        boxes = [{'label': 'formula', 'score': .9, 'coordinate': [20, 40, 200, 100]},
                 {'label': 'formula_number', 'score': .9, 'coordinate': [210, 40, 230, 70]}]
        regions = formula_regions(boxes, 400, 600, 200, 300)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['bbox'], [10, 20, 100, 50])
        self.assertFalse(regions[0]['publishable'])

    def test_invalid_and_low_confidence_boxes_ignored(self):
        boxes = [{'label': 'formula', 'score': .4, 'coordinate': [1, 1, 10, 10]},
                 {'label': 'formula', 'score': .9, 'coordinate': [1, 1, 999, 10]},
                 {'label': 'formula', 'score': float('nan'), 'coordinate': [1, 1, 10, 10]}]
        self.assertEqual(formula_regions(boxes, 100, 100, 50, 50), [])

    def test_dimensions_rejected(self):
        with self.assertRaises(ValueError):
            formula_regions([], 0, 100, 50, 50)

    def test_missing_local_model_does_not_download(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = CachedLayoutEngine(directory)
            with self.assertRaises(FileNotFoundError):
                engine.predict(None)
            self.assertIsNone(engine.pipeline)
