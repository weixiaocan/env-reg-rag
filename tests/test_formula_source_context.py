import unittest
import pymupdf
from src.application.formula_source_context import source_context


class SourceContextTests(unittest.TestCase):
    def test_original_blocks_have_coordinates_and_page_identity(self):
        pdf = pymupdf.open()
        for text in ('Previous definitions', 'Q = A * v\nA: original area (m2)', 'Next conditions'):
            page = pdf.new_page()
            page.insert_text((50, 60), text)
        result = source_context(pdf, 2, [40, 40, 200, 65], 'doc_' + 'a' * 16)
        self.assertEqual([p['physical_page'] for p in result], [1, 2, 3])
        current = result[1]
        self.assertEqual(current['status'], 'unverified_native_text')
        self.assertIn('original area', current['excerpts'][0]['text'])
        self.assertEqual(current['excerpts'][0]['physical_page'], 2)
        self.assertEqual(len(current['excerpts'][0]['bbox']), 4)
        self.assertFalse(current['verified'])
        self.assertTrue(current['source_url'].endswith('#page=2'))
        pdf.close()

    def test_blank_scan_is_not_invented_or_called_missing_definition(self):
        pdf = pymupdf.open()
        pdf.new_page()
        result = source_context(pdf, 1, [1, 1, 20, 20], 'doc_' + 'b' * 16)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'no_native_text_in_window')
        self.assertEqual(result[0]['excerpts'], [])
        pdf.close()

    def test_far_text_excluded_and_invalid_page_rejected(self):
        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((50, 750), 'Unrelated far text')
        self.assertEqual(source_context(pdf, 1, [1, 1, 20, 20], 'doc_' + 'b' * 16)[0]['excerpts'], [])
        with self.assertRaises(ValueError):
            source_context(pdf, 0, [1, 1, 20, 20], 'doc_' + 'b' * 16)
        pdf.close()
