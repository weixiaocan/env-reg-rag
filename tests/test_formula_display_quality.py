import unittest
from src.evaluation.formulas import evaluate_formula_display


class FormulaDisplayQualityTests(unittest.TestCase):
    def test_cjk_wrapper_and_parenthesis_sizing_are_presentation_only(self):
        expected = r'A(\text{圆形})=\frac{1}{2}lR\pm\frac{1}{2}dh'
        actual = r'A\left( 圆形 \right)=\frac{1}{2}l R\pm\frac{1}{2}d h'
        r = evaluate_formula_display(actual, expected)
        self.assertFalse(r['exact_display_match'])
        self.assertTrue(r['presentation_normalized_match'])
        self.assertFalse(r['proves_mathematical_equivalence'])

    def test_sign_decimal_and_variables_are_not_erased(self):
        for actual, expected in [('A=x-y', r'A=x\pm y'), ('A=08x', 'A=0.8x'),
                                 ('A=x_1', 'A=x_i'), ('A=lR', 'A=1R')]:
            self.assertFalse(evaluate_formula_display(actual, expected)['presentation_normalized_match'])

    def test_unknown_macros_are_not_removed(self):
        self.assertFalse(evaluate_formula_display(r'\leftarrow', 'arrow')['presentation_normalized_match'])
        self.assertFalse(evaluate_formula_display(r'\text{x+y}', 'x+y')['presentation_normalized_match'])

    def test_fraction_and_sum_structure_must_match(self):
        for actual, expected in [(r'\frac{a}{b}', r'\frac{b}{a}'),
                                 (r'\sum_{i=0}^{n}x_i', r'\sum_{i=1}^{n}x_i')]:
            self.assertFalse(evaluate_formula_display(actual, expected)['presentation_normalized_match'])

    def test_missing_or_empty_output_never_matches(self):
        for actual, expected in [(None, 'A=x'), ('', ''), ('  ', '  ')]:
            self.assertFalse(evaluate_formula_display(actual, expected)['presentation_normalized_match'])

    def test_exact_match_and_whitespace_keep_existing_contract(self):
        self.assertTrue(evaluate_formula_display('Q = A', 'Q=A')['exact_display_match'])
