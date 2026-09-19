import unittest
from src.application.corpus_update import SourceCatalog
from src.evaluation.unified_pdf_audit import audit_documents, original_page_signals
from tests.test_unified_pdf import UnifiedPdfTests


class UnifiedAuditTests(unittest.TestCase):
    def setUp(self):
        f = UnifiedPdfTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.f = f
        self.catalog = SourceCatalog(1, 1, 0, (f.asset,), {}, 1)

    def test_missing_pages_are_named_with_file_and_physical_page(self):
        report = audit_documents(self.f.root, self.catalog, [])
        issue = report['integrity_issues'][0]
        self.assertEqual(issue['file_name'], 'mixed.pdf')
        self.assertEqual(issue['physical_page'], 1)
        self.assertEqual(issue['reason'], 'page_processing_record_missing')
        self.assertFalse(report['artifact_integrity_passed'])

    def test_original_formula_omission_is_detected_without_parser_candidates(self):
        import pymupdf
        with pymupdf.open(self.f.root / 'data/raw/mixed.pdf') as pdf:
            signals = original_page_signals(pdf[0], [])
        formula = next(s for s in signals if s['reason'] == 'original_math_operator_candidate_not_assigned')
        self.assertIn('Q = A', formula['original_text'])
        self.assertEqual(formula['quality_status'], 'needs_review')

    def test_scan_carrier_records_specific_unverified_page_region(self):
        import pymupdf
        with pymupdf.open(self.f.root / 'data/raw/mixed.pdf') as source:
            image = source[0].get_pixmap(dpi=72, alpha=False).tobytes('png')
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=300, height=400)
            page.insert_image(page.rect, stream=image)
            signals = original_page_signals(page, [])
        carrier = next(s for s in signals if s['reason'] ==
                       'original_full_page_image_layout_semantics_not_independently_verified')
        self.assertEqual(carrier['bbox'], [0, 0, 300, 400])
        self.assertEqual(carrier['quality_status'], 'needs_review')
        self.assertEqual(carrier['source_image_index'], 0)

    def test_original_vector_graphic_detected_without_system_regions(self):
        import pymupdf
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=300, height=400)
            page.draw_rect([20, 30, 150, 140])
            signals = original_page_signals(page, [])
        graphic = next(s for s in signals if s['reason'] == 'original_vector_graphic_candidate_not_assigned')
        self.assertEqual(graphic['bbox'], [20, 30, 150, 140])

    def test_gold_cells_are_compared_to_candidate_structure_not_flattened_text(self):
        import json
        registry = self.f.root / 'data/registry'
        registry.mkdir(parents=True)
        # Values and coordinates come from the independently drawn source PDF.
        (registry / 'table-cell-gold.json').write_text(json.dumps({'anchors': [{
            'anchor_id': 'independent-drawn-table', 'file_sha256': self.f.sha,
            'physical_page': 1, 'table_index': 0,
            'expected_cells': [{'row': 1, 'col': 0, 'text': '1.0'}]}]}), encoding='utf-8')
        page = self.f.extractor().extract(self.f.asset, physical_page=1)
        documents = [{'sha256': self.f.sha, 'pages': [page]}]
        check = audit_documents(self.f.root, self.catalog, documents)['fixed_table_cell_checks'][0]
        self.assertTrue(check['all_checks_passed'])
        self.assertFalse(check['can_approve_transcription'])
        table = next(r for r in page['regions'] if r['kind'] == 'table')['tables'][0]
        next(c for c in table['cells'] if c['row'] == 1 and c['col'] == 0)['text'] = '10'
        check = audit_documents(self.f.root, self.catalog, documents)['fixed_table_cell_checks'][0]
        self.assertFalse(check['all_checks_passed'])
        self.assertEqual(check['checks'][0]['reason'], 'cell_text_mismatch')

    def test_source_cell_check_preserves_decimal_and_requires_exact_value(self):
        import pymupdf
        from src.evaluation.table_source_values import cell_source_checks
        region = {'region_id': 'independent-table', 'tables': [{'cells': [
            {'row': 1, 'col': 0, 'text': '1.0', 'bbox': [20, 320, 84, 348]},
            {'row': 1, 'col': 1, 'text': '0', 'bbox': [20, 320, 84, 348]},
            {'row': 2, 'col': 0, 'text': '2.0', 'bbox': None}]}]}
        with pymupdf.open(self.f.root / 'data/raw/mixed.pdf') as source:
            checks = cell_source_checks(source[0], region)
        self.assertEqual(checks[0]['status'], 'literal_cell_text_matches_original_layer_pending_review')
        self.assertEqual(checks[1]['status'], 'cell_text_differs_from_original_text_layer')
        self.assertEqual(checks[2]['status'], 'original_cell_geometry_not_recovered')
        self.assertFalse(checks[0]['can_approve_transcription'])

    def test_original_image_tamper_fails_independent_pdf_crop_check(self):
        page = self.f.extractor().extract(self.f.asset, physical_page=1)
        r = next(r for r in page['regions'] if r['kind'] == 'image')
        (self.f.root / r['image_ref']).write_bytes(b'changed')
        r['image_sha256'] = __import__('hashlib').sha256(b'changed').hexdigest()
        report = audit_documents(self.f.root, self.catalog, [{'sha256': self.f.sha, 'pages': [page]}])
        self.assertIn('region_image_differs_from_original_pdf_crop', [i['reason'] for i in report['integrity_issues']])
