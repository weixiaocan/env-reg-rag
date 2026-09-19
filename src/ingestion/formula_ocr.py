"""Local, explicit formula-model boundary, separate from routine text OCR."""
import importlib.metadata
from pathlib import Path

PROJECT_MODEL_DIR = Path(__file__).resolve().parents[2] / 'data/model_runtime/models/PP-FormulaNet_plus-M'
MODEL_FILES = ('inference.json', 'inference.pdiparams', 'inference.yml')


class CachedFormulaEngine:
    name = 'paddleocr-formula:PP-FormulaNet_plus-M'

    def __init__(self, model_dir=None):
        self.model_dir = (Path(model_dir) if model_dir is not None else
                          PROJECT_MODEL_DIR if all((PROJECT_MODEL_DIR / f).is_file() for f in MODEL_FILES) else
                          Path.home() / '.paddlex/official_models/PP-FormulaNet_plus-M')
        self.version = importlib.metadata.version('paddleocr')
        self.pipeline = None

    def predict(self, image):
        if not all((self.model_dir / f).is_file() for f in MODEL_FILES):
            raise FileNotFoundError('local formula model is unavailable')
        if self.pipeline is None:
            from paddleocr import FormulaRecognition
            # Paddle's Windows native loader may reject a Unicode absolute
            # project prefix; use a relative path only when under current cwd.
            model_path = self.model_dir
            if model_path.is_absolute():
                try:
                    model_path = model_path.relative_to(Path.cwd())
                except ValueError:
                    pass
            self.pipeline = FormulaRecognition(model_name='PP-FormulaNet_plus-M',
                                               model_dir=str(model_path), device='cpu',
                                               enable_mkldnn=False, cpu_threads=2)
        results = list(self.pipeline.predict(input=image))
        if len(results) != 1:
            raise ValueError('expected one formula-region prediction')
        return results[0]['rec_formula']
