"""Independent synthetic PDF geometry; expected boxes are not parser answers."""
import hashlib
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import pymupdf

from src.application.corpus_update import AutomaticPageExtractor, ContentAsset, CorpusUpdateService, build_source_catalog
from src.application.unified_pdf import UnifiedPageExtractor


class UnifiedPdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'data/raw').mkdir(parents=True)
        pdf = pymupdf.open()
        p = pdf.new_page(width=300, height=400)
        p.insert_text((20, 30), 'Independent body text with sufficient characters.')
        p.insert_text((20, 100), 'Q = A * v (1)')
        p.draw_rect((20, 180, 150, 250))
        p.insert_text((20, 275), 'Figure without a caption identifier')
        p.draw_rect((20, 280, 150, 350))
        p.draw_line((20, 310), (150, 310))
        p.draw_line((85, 310), (85, 350))
        p.insert_text((25, 300), 'Unit m')
        p.insert_text((25, 335), '1.0')
        p.insert_text((90, 335), '2.0')
        pdf.save(self.root / 'data/raw/mixed.pdf')
        pdf.close()
        self.sha = hashlib.sha256((self.root / 'data/raw/mixed.pdf').read_bytes()).hexdigest()
        self.asset = ContentAsset(self.sha, 'data/raw/mixed.pdf', 'mixed.pdf', 1, (), {})
        # Coordinates are manually specified from the generated original PDF.
        self.layout = Mock()
        self.layout.predict.return_value = [
            {'label': 'text', 'score': .95, 'coordinate': [40, 30, 600, 100]},
            {'label': 'formula', 'score': .95, 'coordinate': [40, 160, 350, 210]},
            {'label': 'image', 'score': .95, 'coordinate': [40, 360, 300, 500]},
            {'label': 'table', 'score': .95, 'coordinate': [40, 560, 300, 700]},
        ]
        boxes = self.layout.predict.return_value
        self.layout.predict.side_effect = lambda image: [
            {**b, 'coordinate': [b['coordinate'][0] * image.shape[1] / 600,
                                b['coordinate'][1] * image.shape[0] / 800,
                                b['coordinate'][2] * image.shape[1] / 600,
                                b['coordinate'][3] * image.shape[0] / 800]} for b in boxes]
        self.formula = Mock()
        self.formula.predict.return_value = 'Q=A v'
        self.tables = Mock()
        self.tables.parse_page.return_value = {
            'physical_page': 1, 'width': 300, 'height': 400, 'elements': [],
            'raw': {'render_width': 600, 'render_height': 800},
            'tables': [{'table_id': 'table-1', 'html': '<table><tr><th colspan="2">Unit m</th></tr><tr><td>1.0</td><td>2.0</td></tr></table>',
                        'raw': {'bbox': [40, 560, 300, 700]}}]}

    def extractor(self, **kwargs):
        return UnifiedPageExtractor(self.root,
            AutomaticPageExtractor(project_root=self.root, enable_ocr=False),
            layout=self.layout, formula=self.formula, tables=self.tables,
            model_config={'fixture': kwargs.pop('revision', 'v1')}, **kwargs)

    def test_native_page_does_not_skip_four_types(self):
        page = self.extractor().extract(self.asset, physical_page=1)
        self.assertEqual(set(page['content_types']), {'text', 'formula', 'table', 'image'})
        self.assertEqual(len(page['regions']), 4)
        self.assertEqual(page['layout_status'], 'completed')
        image = next(r for r in page['regions'] if r['kind'] == 'image')
        self.assertIn('caption_not_found', image['issues'])
        self.assertTrue((self.root / image['image_ref']).is_file())
        self.assertFalse(image['publishable'])
        table = next(r for r in page['regions'] if r['kind'] == 'table')
        self.assertEqual(table['tables'][0]['cells'][0]['col_span'], 2)

    def test_scanned_page_still_routes_body_table_formula_and_image(self):
        with pymupdf.open(self.root / 'data/raw/mixed.pdf') as original:
            pixmap = original[0].get_pixmap(dpi=150)
        scanned = pymupdf.open()
        scanned.new_page(width=300, height=400).insert_image((0, 0, 300, 400), pixmap=pixmap)
        path = self.root / 'data/raw/scanned.pdf'
        scanned.save(path)
        scanned.close()
        with pymupdf.open(path) as independent_source:
            self.assertEqual(independent_source[0].get_text(), '')
        asset = ContentAsset(hashlib.sha256(path.read_bytes()).hexdigest(), 'data/raw/scanned.pdf', 'scanned.pdf', 1, (), {})
        text = AutomaticPageExtractor(project_root=self.root, enable_ocr=True)
        text.ocr = Mock()
        text.ocr.parse_page.return_value = {
            'physical_page': 1, 'page_index': 0, 'width': 300, 'height': 400, 'rotation': 0,
            'extraction_route': 'full_ocr', 'text': 'Independent body text with sufficient characters.',
            'quality': {'status': 'pass', 'reasons': []}, 'tables': [],
            'elements': [{'element_id': 'p0000-text0', 'text': 'Independent body text with sufficient characters.',
                          'bbox': [20, 16, 280, 40]}],
        }
        parser = UnifiedPageExtractor(self.root, text, layout=self.layout, formula=self.formula,
                                     tables=self.tables, model_config={'fixture': 'scan-v1'})
        page = parser.extract(asset, physical_page=1)
        text.ocr.parse_page.assert_called_once()
        self.assertEqual(page['extraction_route'], 'full_ocr')
        self.assertEqual({r['kind'] for r in page['regions']}, {'text', 'table', 'formula', 'image'})
        self.assertTrue(page['structure_execution_complete'])

    def test_empty_table_is_not_execution_complete(self):
        self.tables.parse_page.return_value['tables'][0]['html'] = '<table></table>'
        page = self.extractor().extract(self.asset, physical_page=1)
        table = next(r for r in page['regions'] if r['kind'] == 'table')
        self.assertEqual(table['execution_status'], 'failed')
        self.assertIn('table_cells_not_recovered', table['issues'])
        self.assertFalse(page['structure_execution_complete'])
        self.extractor().extract(self.asset, physical_page=1)
        self.assertEqual(self.tables.parse_page.call_count, 2)
        self.assertEqual(self.layout.predict.call_count, 1)

    def test_corrupt_page_checkpoint_restores_regions_from_valid_stages(self):
        import json
        from src.application.corpus_update import page_extractor_config_hash
        extractor = self.extractor()
        service = CorpusUpdateService(self.root, page_extractor=extractor)
        key = page_extractor_config_hash(extractor)
        service._page(self.asset, key, 1, extractor)
        path = service._page_cache_path(self.asset, key, 1)
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['regions'] = []
        path.write_text(json.dumps(payload), encoding='utf-8')
        restored, reused = service._page(self.asset, key, 1, extractor)
        self.assertFalse(reused)
        self.assertEqual(len(restored['regions']), 4)
        self.assertEqual(self.layout.predict.call_count, 1)
        self.assertEqual(self.formula.predict.call_count, 1)

    def test_unchanged_reuse_and_config_change(self):
        self.extractor().extract(self.asset, physical_page=1)
        self.extractor().extract(self.asset, physical_page=1)
        self.assertEqual(self.layout.predict.call_count, 1)
        self.assertEqual(self.formula.predict.call_count, 1)
        self.extractor(revision='v2').extract(self.asset, physical_page=1)
        self.assertEqual(self.layout.predict.call_count, 2)

    def test_table_normalization_change_reuses_verified_raw_html_and_model_geometry(self):
        import json
        from src.application.unified_pdf import fingerprint
        extractor = self.extractor()
        extractor.extract(self.asset, physical_page=1)
        directory = self.root / 'data/model_runtime/unified_pdf' / self.sha / '1'
        current = next(directory.glob('tables-*.json'))
        saved = json.loads(current.read_text(encoding='utf-8'))
        old_key = fingerprint({'profile': 'known-table-region-mobile-v3:200dpi:padding2',
                               'stage': 'tables', 'config': saved['configuration']})
        saved['config_hash'] = old_key
        (directory / ('tables-' + old_key[:16] + '.json')).write_text(json.dumps(saved), encoding='utf-8')
        current.unlink()
        page = extractor.extract(self.asset, physical_page=1)
        self.assertEqual(self.tables.parse_page.call_count, 1)
        self.assertTrue(page['structure_execution_complete'])
        migrated = json.loads(current.read_text(encoding='utf-8'))
        self.assertIn('normalization_reused_from', migrated)

    def test_repeated_v4_table_timeout_uses_v5_fallback_without_model_retry(self):
        import json
        from src.application.unified_pdf import fingerprint
        extractor = self.extractor()
        directory = self.root / 'data/model_runtime/unified_pdf' / self.sha / '1'
        config = {'models': {'fixture': 'v1'}, 'expected_boxes': [[20, 280, 150, 350]],
                  'layout_config_hash': 'layout', 'original_context_hash': 'text'}
        old_profile = 'known-table-region-mobile-v4:200dpi:padding2:literal-inequality-html'
        old_key = fingerprint({'profile': old_profile, 'stage': 'tables', 'config': config})
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ('tables-' + old_key[:16] + '.json')).write_text(json.dumps({
            'execution_status': 'failed', 'reason': 'TimeoutError', 'result': None,
            'config_hash': old_key, 'configuration': config}), encoding='utf-8')
        primary = Mock(side_effect=AssertionError('timed-out model must not run a third time'))
        fallback = Mock(return_value=[{'cells': [{'row': 0, 'col': 0, 'row_span': 1,
            'col_span': 1, 'text': 'source', 'bbox': [20, 280, 50, 295]}],
            'bbox': [20, 280, 150, 350]}])
        result = extractor._stage(directory, 'tables', config, primary,
                                  timeout_fallback=fallback)
        self.assertEqual(result['execution_status'], 'completed')
        primary.assert_not_called()
        fallback.assert_called_once()

    def test_failed_formula_retries_without_layout_rerun(self):
        self.formula.predict.side_effect = FileNotFoundError()
        page = self.extractor().extract(self.asset, physical_page=1)
        self.assertEqual(page['content_types']['formula']['execution_status'], 'failed')
        self.formula.predict.side_effect = None
        page = self.extractor().extract(self.asset, physical_page=1)
        self.assertEqual(page['content_types']['formula']['execution_status'], 'completed')
        self.assertEqual(self.layout.predict.call_count, 1)
        self.assertEqual(self.formula.predict.call_count, 2)

    def test_modified_source_rejected(self):
        with (self.root / 'data/raw/mixed.pdf').open('ab') as f:
            f.write(b'changed')
        with self.assertRaises(ValueError):
            self.extractor().extract(self.asset, physical_page=1)

    def test_native_rotation_coordinates_match_rendered_page(self):
        from src.ingestion.native_pdf import NativePdfParser
        source = self.root / 'data/raw/rotated.pdf'
        pdf = pymupdf.open()
        page = pdf.new_page(width=300, height=400)
        page.insert_text((20, 100), 'ROTATED BODY')
        page.set_rotation(270)
        pdf.save(source)
        pdf.close()
        parsed = NativePdfParser().parse_page(source, physical_page=1)
        box = parsed['elements'][0]['bbox']
        # Original baseline (20,100) moves to (100,280) in the 400x300 viewport.
        self.assertAlmostEqual(box[3], 280, delta=2)
        self.assertAlmostEqual(box[0], 88, delta=2)
        self.assertLessEqual(box[2], 400)
        self.assertLessEqual(box[3], 300)
        self.assertEqual(parsed['coordinate_normalizations'][0]['rotation'], 270)

    def test_full_candidate_and_add_modified_unchanged_input(self):
        registry = self.root / 'data/registry'
        registry.mkdir(parents=True)
        def inventory(paths):
            with (registry / 'inventory.csv').open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['file_name', 'rel_path', 'sha256', 'pages'])
                writer.writeheader()
                for path in paths:
                    writer.writerow({'file_name': path.name, 'rel_path': path.relative_to(self.root).as_posix(),
                                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'pages': 1})
        original = self.root / 'data/raw/mixed.pdf'
        inventory([original])
        first = CorpusUpdateService(self.root, page_extractor=self.extractor()).build()
        self.assertEqual(first['structure_processing']['complete_page_count'], 1)
        self.assertEqual(first['status'], 'ready')
        chunks = [__import__('json').loads(line) for line in (self.root / first['retrieval_chunks']).read_text(encoding='utf-8').splitlines()]
        structural = [c for c in chunks if c['metadata'].get('source_regions')]
        self.assertEqual(len(structural), 3)
        self.assertTrue(all(c['metadata']['usage_policy'] == 'source_locator_only' for c in structural))
        again = CorpusUpdateService(self.root, page_extractor=self.extractor()).build()
        self.assertEqual(first['corpus_version'], again['corpus_version'])
        self.assertEqual(again['reused_content_count'], 1)
        added = self.root / 'data/raw/added.pdf'
        pdf = pymupdf.open(original)
        pdf[0].insert_text((20, 380), 'New original source')
        pdf.save(added)
        pdf.close()
        inventory([original, added])
        new = CorpusUpdateService(self.root, page_extractor=self.extractor()).build()
        self.assertEqual(new['reused_content_count'], 1)
        self.assertEqual(new['processed_content_count'], 1)
        self.assertEqual(new['page_count'], 2)
        pdf = pymupdf.open(added)
        changed = self.root / 'data/raw/modified.pdf'
        pdf[0].insert_text((20, 365), 'Changed source body')
        pdf.save(changed)
        pdf.close()
        inventory([original, changed])
        updated = CorpusUpdateService(self.root, page_extractor=self.extractor()).build()
        self.assertNotEqual(new['corpus_version'], updated['corpus_version'])
        self.assertEqual(updated['processed_content_count'], 1)
