"""Local page-layout detection; no gold coordinates or transcription used."""
import math
from numbers import Real
from pathlib import Path


def formula_regions(boxes, pixel_width, pixel_height, page_width, page_height):
    dimensions = (pixel_width, pixel_height, page_width, page_height)
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in dimensions):
        raise ValueError('invalid page dimensions')
    regions = []
    for box in boxes:
        score = box.get('score')
        coords = box.get('coordinate')
        if box.get('label') != 'formula' or type(score) not in (int, float) or not math.isfinite(score) or not .5 <= score <= 1:
            continue
        if not isinstance(coords, (list, tuple)) or len(coords) != 4 or any(not isinstance(v, Real) or isinstance(v, bool) or not math.isfinite(v) for v in coords):
            continue
        x0, y0, x1, y1 = map(float, coords)
        if not (0 <= x0 < x1 <= pixel_width and 0 <= y0 < y1 <= pixel_height):
            continue
        regions.append({'bbox': [x0 * page_width / pixel_width, y0 * page_height / pixel_height,
                                 x1 * page_width / pixel_width, y1 * page_height / pixel_height],
                        'pixel_bbox': [x0, y0, x1, y1], 'score': score, 'kind': 'formula',
                        'review_status': 'pending_review',
                        # publishable here is a review-subsystem status flag, NOT a V2
                        # contract field; formula_regions feeds the review path.
                        'publishable': False,
                        'can_use_for_calculation': False})
    return regions


class CachedLayoutEngine:
    name = 'PP-DocLayout_plus-L'

    def __init__(self, model_dir=None):
        self.model_dir = Path(model_dir) if model_dir else Path.home() / '.paddlex/official_models' / self.name
        self.pipeline = None

    def predict(self, image):
        if not all((self.model_dir / f).is_file() for f in ('inference.json', 'inference.pdiparams', 'inference.yml')):
            raise FileNotFoundError('local layout model unavailable')
        if self.pipeline is None:
            from paddleocr import LayoutDetection
            self.pipeline = LayoutDetection(model_name=self.name, model_dir=str(self.model_dir),
                                            device='cpu', enable_mkldnn=False, cpu_threads=2)
        results = list(self.pipeline.predict(input=image))
        if len(results) != 1:
            raise ValueError('expected one page layout result')
        return results[0]['boxes']
