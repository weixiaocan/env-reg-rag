import copy
from pathlib import Path
import tempfile
import unittest

import pymupdf

from src.application.formula_context_relations import attach_cross_page_formula_sources
from src.ingestion.native_pdf import NativePdfParser


class FormulaContextRelationsTests(unittest.TestCase):
    def fixture(self, marker):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'independent-source.pdf'
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=400)
                page.insert_text((20, 330), 'Q = v A (3.2-1)')
                page = pdf.new_page(width=300, height=400)
                page.insert_text((20, 35), marker, fontname='china-s', fontsize=10)
                pdf.save(source)
            pages = [NativePdfParser().parse_page(source, physical_page=n) for n in (1, 2)]
        formula = {'kind': 'formula', 'region_id': 'independent-equation', 'physical_page': 1,
                   'bbox': [20, 310, 150, 340], 'math_category': 'relation_candidate',
                   'formula_number_candidates': [{'number': '3.2-1'}], 'relations': [],
                   'issues': [], 'context': [], 'parameter_reading': {'excerpts': []}}
        pages[0]['regions'] = [formula]
        pages[1]['regions'] = []
        return {'pages': pages}, formula

    def test_original_numbered_marker_links_cross_page_with_original_units(self):
        document, formula = self.fixture('式(3.2-1)中 Q——流量(m3/s)')
        original = document['pages'][1]['elements'][0]['text']
        attach_cross_page_formula_sources(document)
        relation = formula['relations'][0]
        self.assertEqual(relation['kind'], 'explicit_numbered_formula_parameter_reference')
        self.assertEqual(relation['source']['physical_page'], 2)
        self.assertEqual(relation['source']['text'], original)
        self.assertIn('m3/s', original)
        self.assertFalse(relation['verified'])
        self.assertEqual(formula['parameter_reading']['excerpts'][0]['text'], original)

    def test_bare_marker_keeps_uncertainty_without_associated_retrieval_text(self):
        document, formula = self.fixture('式中：Q——流量(m3/s)')
        attach_cross_page_formula_sources(document)
        self.assertEqual(formula['relations'][0]['association_status'], 'uncertain')
        self.assertEqual(formula['relations'][0]['source']['physical_page'], 2)
        self.assertEqual(formula['context'], [])
        self.assertEqual(formula['parameter_reading']['excerpts'], [])

    def test_repeated_number_does_not_choose_a_formula(self):
        document, formula = self.fixture('式(3.2-1)中 Q——流量(m3/s)')
        other = copy.deepcopy(formula)
        other['region_id'] = 'independent-other-equation'
        document['pages'][0]['regions'].append(other)
        attach_cross_page_formula_sources(document)
        for region in (formula, other):
            self.assertEqual(region['relations'][0]['kind'], 'ambiguous_numbered_formula_parameter_reference')
            self.assertEqual(region['parameter_reading']['excerpts'], [])


if __name__ == '__main__':
    unittest.main()
