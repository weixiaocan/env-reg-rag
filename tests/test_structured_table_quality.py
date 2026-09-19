import unittest

from src.evaluation.structured_tables import evaluate_cells


class StructuredTableQualityTests(unittest.TestCase):
    def cells(self):
        return [{'row': 0, 'col': 0, 'text': '磷酸盐'},
                {'row': 0, 'col': 1, 'text': '≥8.0 mg/L'},
                {'row': 1, 'col': 0, 'text': '氟化物'},
                {'row': 1, 'col': 1, 'text': '≥1.0 mg/L'}]

    def test_formatting_can_change_without_changing_numbers(self):
        expected = self.cells()
        actual = self.cells()
        actual[1]['text'] = '>= 8.0mg/L'
        self.assertTrue(evaluate_cells(actual, expected)['all_checks_passed'])

    def test_decimal_operator_and_unit_errors_fail(self):
        for text in ['≥80mg/L', '>8.0mg/L', '≤8.0mg/L', '≥8.0g/L']:
            actual = self.cells()
            actual[1]['text'] = text
            self.assertFalse(evaluate_cells(actual, self.cells())['all_checks_passed'])

    def test_correct_fragments_in_wrong_rows_do_not_pass(self):
        actual = self.cells()
        actual[1]['text'], actual[3]['text'] = actual[3]['text'], actual[1]['text']
        self.assertFalse(evaluate_cells(actual, self.cells())['all_checks_passed'])

    def test_missing_structure_never_uses_flat_page_text(self):
        result = evaluate_cells([], self.cells())
        self.assertEqual(result['passed_check_count'], 0)
        self.assertFalse(result['can_approve_transcription'])

    def test_merged_header_coordinates_are_respected(self):
        cells = [{'row': 0, 'col': 0, 'row_span': 1, 'col_span': 2, 'text': '单位 mg/L'}]
        result = evaluate_cells(cells, [{'row': 0, 'col': 1, 'text': '单位mg/L'}])
        self.assertTrue(result['all_checks_passed'])
        self.assertFalse(result['can_approve_transcription'])

    def test_overlaps_and_invalid_spans_fail_closed(self):
        for cells in [self.cells() + [self.cells()[0]],
                      [{'row': 0, 'col': 0, 'col_span': 0, 'text': '磷酸盐'}],
                      [{'row': False, 'col': 0, 'text': '磷酸盐'}]]:
            self.assertFalse(evaluate_cells(cells, self.cells())['all_checks_passed'])

    def test_invalid_gold_is_not_an_empty_success(self):
        for expected in [[], [{'row': 0, 'col': 0, 'text': ''}], self.cells() + [self.cells()[0]]]:
            with self.assertRaises(ValueError):
                evaluate_cells(self.cells(), expected)


if __name__ == '__main__':
    unittest.main()
