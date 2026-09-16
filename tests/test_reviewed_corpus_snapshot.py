"""Hash-bound acceptance evidence for the reviewed CECS scan pair."""
import json
from pathlib import Path
import unittest

from scripts.update_corpus import plan
from src.application.corpus_update import build_source_catalog

ROOT = Path(__file__).resolve().parents[1]
A = '468227cff7b0c938d526c89f73a077c49d272e56aca936ef710c32571ede1163'
B = 'f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941'


class ReviewedCorpusSnapshotTests(unittest.TestCase):
    def test_confirmation_has_complete_physical_page_coverage(self):
        payload = json.loads((ROOT / 'data/registry/document_relations.json').read_text(encoding='utf-8'))
        relation = next(r for r in payload['relations'] if set(r['member_sha256']) == {A, B})
        self.assertEqual(relation['status'], 'confirmed')
        self.assertEqual(relation['primary_sha256'], B)
        comparison = relation['comparison']
        self.assertEqual(comparison['result'], 'identical_body')
        coverage = comparison['visual_coverage']
        self.assertEqual(coverage['paired_page_count'], 70)
        self.assertEqual(coverage['left_physical_pages'], [2, 71])
        self.assertEqual(coverage['right_physical_pages'], [6, 75])
        self.assertEqual(coverage['right_additional_front_pages'], [2, 3, 4, 5])
        self.assertEqual(coverage['separately_reviewed_covers'], [1, 1])

    def test_plan_preserves_originals_and_selects_one_primary(self):
        result = plan(ROOT)
        self.assertEqual((result['source_file_count'], result['unique_content_count'],
                          result['duplicate_copy_count']), (27, 22, 5))
        self.assertEqual((result['selected_content_count'], result['same_version_copy_count']), (21, 1))
        self.assertEqual((result['unique_page_count'], result['selected_page_count']), (1149, 1078))

    def test_review_does_not_promote_official_source_or_redirect_old_pages(self):
        catalog = build_source_catalog(ROOT)
        decision = next(d for d in catalog.document_relations['decisions'] if A in d['member_sha256'])
        self.assertEqual(decision['selection_reason'], 'explicit_review')
        primary = next(a for a in catalog.assets if a.sha256 == B)
        self.assertNotEqual(primary.metadata['source_review'], 'official_fulltext_verified')
        mapping = next(m for m in catalog.document_relations['file_mappings'] if m['file_sha256'] == A)
        self.assertEqual(mapping['page_mapping_status'], 'not_established')


if __name__ == '__main__':
    unittest.main()
