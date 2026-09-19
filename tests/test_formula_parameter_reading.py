import unittest
from src.application.formula_parameter_reading import parameter_reading


class ParameterReadingTests(unittest.TestCase):
    def context(self, lines, page=2):
        return [{'physical_page': page, 'excerpts': [
            {'text': text, 'bbox': [10, top, 200, top+10]}
            for top, text in lines]}]

    def test_explicit_marker_preserves_literal_units_and_provenance(self):
        r = parameter_reading(self.context([(80, '别的定义'), (120, '式中：'),
            (140, 'Q——流量（m³/d）'), (160, 'A——面积（m²）'),
            (180, '7.6.12 新条文')]), 2, [10, 90, 200, 110])
        self.assertEqual([e['text'] for e in r['excerpts']],
                         ['式中：', 'Q——流量（m³/d）', 'A——面积（m²）'])
        self.assertFalse(r['excerpts'][1]['verified'])
        self.assertEqual(r['excerpts'][1]['physical_page'], 2)

    def test_next_formula_and_cross_page_are_not_joined(self):
        context = self.context([(120, '式中：Q——流量'), (160, '另一公式参数')])
        r = parameter_reading(context, 2, [10, 90, 200, 110], 150)
        self.assertEqual(len(r['excerpts']), 1)
        self.assertEqual(parameter_reading(context, 1, [10, 90, 200, 110])['status'], 'not_located')

    def test_without_explicit_marker_does_not_guess(self):
        r = parameter_reading(self.context([(120, 'Q——流量')]), 2, [10, 90, 200, 110])
        self.assertEqual(r['excerpts'], [])
