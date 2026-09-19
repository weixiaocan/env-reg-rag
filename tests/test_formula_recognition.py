import json
import unittest
from unittest.mock import patch
from tests import test_corpus_update as corpus_fixture
from scripts.recognize_pdf_formula import recognize
from src.ingestion.formula_ocr import CachedFormulaEngine


class Engine:
    name, version = 'fixture', '1'

    def predict(self, image):
        return r'Q=\frac{1}{n}\sum_{i=1}^{n}A'


class FormulaRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = corpus_fixture.CorpusUpdateTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.row = self.fixture.row(name='formula.pdf', rel='data/raw/资料/formula.pdf', data=b'fixture')
        self.fixture.write_inventory([self.row])
        self.anchor = {'anchor_id': 'flow', 'file_sha256': self.row['sha256'], 'physical_page': 1,
                       'bbox': [10, 10, 90, 30], 'context_bbox': [10, 35, 90, 80],
                       'coordinate_system': 'page_points_top_left', 'formula_number': '1',
                       'expected_latex': Engine().predict(None)}
        self.gold = self.root / 'data/registry/formula-gold.json'
        self.save()

    def save(self):
        self.gold.write_text(json.dumps({'schema_version': '1', 'anchors': [self.anchor]}), encoding='utf-8')

    def tearDown(self):
        self.fixture.tearDown()

    def render(self, *args):
        return None, '式中：A——面积(m²)', [10, 35, 90, 80]

    def test_success_is_diagnostic_and_never_executable(self):
        r = recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
        self.assertEqual(r['raw_latex'], self.anchor['expected_latex'])
        self.assertTrue(r['gold_check']['exact_display_match'])
        self.assertEqual(r['context_evidence']['text'], '式中：A——面积(m²)')
        self.assertFalse(r['can_use_for_calculation'])
        self.assertFalse(r['publishable'])
        self.assertNotIn('expected_latex', r)
        self.assertIsNone(r['executable_expression'])
        self.assertFalse((self.root / 'data/registry/corpus-current.json').exists())

    def test_failure_keeps_original_location_and_hides_message(self):
        with patch.object(Engine, 'predict', side_effect=RuntimeError('private provider detail')):
            r = recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
        self.assertEqual(r['status'], 'source_page_only')
        self.assertEqual(r['bbox'], self.anchor['bbox'])
        self.assertIsNone(r['raw_latex'])
        self.assertNotIn('private provider', str(r))

    def test_bad_region_and_page_are_rejected_before_engine(self):
        for field, value in [('bbox', [10, 30, 90, 10]), ('bbox', [0, 0, float('nan'), 1]),
                             ('physical_page', True), ('physical_page', 2),
                             ('coordinate_system', 'pixels')]:
            old = self.anchor[field]
            self.anchor[field] = value
            self.save()
            with self.assertRaises(ValueError):
                recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
            self.anchor[field] = old

    def test_stale_source_is_rejected(self):
        (self.root / self.row['rel_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            recognize(self.root, 'flow', engine=Engine(), renderer=self.render)

    def test_missing_model_does_not_load_or_download(self):
        with self.assertRaises(FileNotFoundError):
            CachedFormulaEngine(self.root / 'no-model').predict(None)

    def test_project_cache_precedes_user_cache(self):
        for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            (self.root / name).write_bytes(b'fixture')
        with patch('src.ingestion.formula_ocr.PROJECT_MODEL_DIR', self.root), patch('pathlib.Path.home', return_value=self.root / 'user'):
            self.assertEqual(CachedFormulaEngine().model_dir, self.root)

    def test_partial_project_download_does_not_hide_user_cache(self):
        with patch('src.ingestion.formula_ocr.PROJECT_MODEL_DIR', self.root), patch('pathlib.Path.home', return_value=self.root / 'user'):
            self.assertEqual(CachedFormulaEngine().model_dir, self.root / 'user/.paddlex/official_models/PP-FormulaNet_plus-M')

    def test_loaded_model_is_reused_without_reinitialization(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        model = self.root / 'model'
        model.mkdir()
        for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            (model / name).write_bytes(b'fixture')
        pipeline = MagicMock()
        pipeline.predict.return_value = [{'rec_formula': 'Q=A'}]
        factory = MagicMock(return_value=pipeline)
        with patch.dict('sys.modules', {'paddleocr': SimpleNamespace(FormulaRecognition=factory)}):
            backend = CachedFormulaEngine(model)
            self.assertEqual(backend.predict(None), 'Q=A')
            self.assertEqual(backend.predict(None), 'Q=A')
        factory.assert_called_once()
        self.assertEqual(factory.call_args.kwargs['model_dir'], str(model))

    def test_model_under_working_directory_uses_relative_loader_path(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        model = self.root / 'model'
        model.mkdir()
        for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
            (model / name).write_bytes(b'fixture')
        pipeline = MagicMock()
        pipeline.predict.return_value = [{'rec_formula': 'Q=A'}]
        factory = MagicMock(return_value=pipeline)
        with patch.dict('sys.modules', {'paddleocr': SimpleNamespace(FormulaRecognition=factory)}), patch('pathlib.Path.cwd', return_value=self.root):
            CachedFormulaEngine(model).predict(None)
        self.assertEqual(factory.call_args.kwargs['model_dir'], 'model')

    def test_empty_output_is_not_success(self):
        with patch.object(Engine, 'predict', return_value=' '):
            r = recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
        self.assertEqual(r['status'], 'source_page_only')
        self.assertFalse(r['gold_check']['exact_display_match'])

    def test_plus_minus_cannot_be_replaced_by_fixed_minus(self):
        self.anchor['expected_latex'] = r'A=\frac{1}{2}lR\pm\frac{1}{2}dh'
        self.save()
        wrong = r'A=\frac{1}{2}lR-\frac{1}{2}dh'
        with patch.object(Engine, 'predict', return_value=wrong):
            r = recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
        self.assertFalse(r['gold_check']['exact_display_match'])
        self.assertEqual(r['raw_latex'], wrong)
        self.assertFalse(r['can_use_for_calculation'])

    def test_decimal_and_variable_changes_do_not_match(self):
        self.anchor['expected_latex'] = r'Q=0.8L_i/\Delta t_i'
        self.save()
        for wrong in [r'Q=08L_i/\Delta t_i', r'Q=0.8L_1/\Delta t_i']:
            with patch.object(Engine, 'predict', return_value=wrong):
                r = recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
            self.assertFalse(r['gold_check']['exact_display_match'])

    def test_render_failure_does_not_claim_valid_region_coordinates(self):
        def failed_render(*args):
            raise ValueError('region exceeds original page')
        r = recognize(self.root, 'flow', engine=Engine(), renderer=failed_render)
        self.assertIsNone(r['bbox'])
        self.assertEqual(r['requested_bbox'], self.anchor['bbox'])
        self.assertEqual(r['status'], 'source_page_only')

    def test_modified_source_during_inference_is_rejected(self):
        def modify(image):
            (self.root / self.row['rel_path']).write_bytes(b'changed')
            return 'Q=A'
        with patch.object(Engine, 'predict', side_effect=modify):
            with self.assertRaises(ValueError):
                recognize(self.root, 'flow', engine=Engine(), renderer=self.render)
