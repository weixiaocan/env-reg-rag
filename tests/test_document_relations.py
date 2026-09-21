from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tests import _pdf_fixture as corpus_fixture
from src.application.corpus_update import build_source_catalog, CorpusUpdateService
from src.application.document_relations import candidates


class DocumentRelationsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = corpus_fixture.CorpusUpdateTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.rows = [self.fixture.row(name=n + '.pdf', rel='data/raw/资料/' + n + '.pdf',
                                     data=n.encode(), std_no='GB 1-2026') for n in ('a', 'b')]
        self.fixture.write_inventory(self.rows)

    def tearDown(self):
        self.fixture.tearDown()

    def relation(self, **changes):
        result = dict(document_id='drainage', document_version_id='drainage-2026',
                      identity={'document_kind': 'standard', 'standard_number': 'GB 1-2026',
                                'source_authority': '发布机关', 'revision': 'original'},
                      member_sha256=[r['sha256'] for r in self.rows],
                      primary_sha256=self.rows[0]['sha256'], status='confirmed',
                      checked_at='2026-09-16T00:00:00+00:00',
                      comparison={'method': 'manual_full_document_comparison',
                                  'result': 'layout_only', 'note': '全文比对，仅排版差异',
                                  'compared_sha256': [r['sha256'] for r in self.rows]})
        result.update(changes)
        (self.root / 'data/registry/document_relations.json').write_text(
            json.dumps({'schema_version': '1', 'relations': [result]}), encoding='utf-8')

    def test_candidates_do_not_exclude_content(self):
        catalog = build_source_catalog(self.root)
        self.assertEqual(len(candidates(catalog.assets)), 1)
        self.assertEqual(len(catalog.assets), 2)

    def test_confirmed_relation_processes_once_and_preserves_hash_id_mapping(self):
        self.relation()
        catalog = build_source_catalog(self.root)
        self.assertEqual(catalog.unique_content_count, 2)
        self.assertEqual(len(catalog.assets), 1)
        self.assertEqual(catalog.duplicate_copy_count, 0)
        self.assertEqual(catalog.unique_page_count, 2)
        self.assertEqual(len(catalog.assets[0].source_files), 1)
        mapping = catalog.fingerprint_payload()['document_relations']
        self.assertEqual(len(mapping['file_mappings']), 2)
        secondary = next(m for m in mapping['file_mappings'] if m['file_sha256'] == self.rows[1]['sha256'])
        self.assertEqual(secondary['page_mapping_status'], 'not_established')
        service = CorpusUpdateService(self.root, page_extractor=corpus_fixture.FakePageExtractor())
        first = service.build()
        self.assertEqual(first['processed_content_count'], 1)
        self.assertEqual(first['selected_content_count'], 1)
        self.assertEqual(service.build()['processed_content_count'], 0)

    def test_unconfirmed_or_conflicting_relations_do_not_merge(self):
        for status in ('candidate', 'conflicting'):
            self.relation(status=status)
            self.assertEqual(len(build_source_catalog(self.root).assets), 2)

    def test_revision_and_substantive_difference_cannot_be_merged(self):
        self.rows[1]['std_no'] = 'GB 1-2025'
        self.fixture.write_inventory(self.rows)
        self.assertEqual(candidates(build_source_catalog(self.root).assets), [])
        self.relation()
        with self.assertRaises(ValueError):
            build_source_catalog(self.root)
        self.rows[1]['std_no'] = 'GB 1-2026'
        self.fixture.write_inventory(self.rows)
        relation = {'method': 'manual_full_document_comparison', 'result': 'substantive_difference',
                    'note': '正文差异', 'compared_sha256': [r['sha256'] for r in self.rows]}
        self.relation(comparison=relation)
        with self.assertRaises(ValueError):
            build_source_catalog(self.root)

    def test_stale_hash_missing_identity_and_fake_time_fail_closed(self):
        for changes in ({'member_sha256': ['0' * 64, self.rows[0]['sha256']]},
                        {'identity': {}}, {'checked_at': '2026-09-16'},
                        {'primary_sha256': '0' * 64}):
            self.relation(**changes)
            with self.assertRaises(ValueError):
                build_source_catalog(self.root)

    def test_verified_official_content_wins_only_after_same_version_confirmation(self):
        from src.application.document_relations import apply_relations
        self.relation()
        assets = build_source_catalog(self.root, apply_document_relations=False).assets
        official = next(a for a in assets if a.sha256 == self.rows[1]['sha256'])
        metadata = dict(official.metadata, source_review='official_fulltext_verified',
                        official_source_uri='https://example.org/official.pdf',
                        source_field_statuses=json.dumps({'source_review': 'verified', 'official_source_uri': 'verified'}))
        assets = tuple(replace(a, metadata=metadata) if a.sha256 == official.sha256 else a for a in assets)
        selected, _ = apply_relations(self.root, assets)
        self.assertEqual(selected[0].sha256, official.sha256)
        self.assertNotEqual(selected[0].metadata['selection_status'], 'approved_formal')

    def test_native_comparison_is_diagnostic_and_scan_coverage_is_unknown(self):
        import pymupdf
        from src.application.document_relations import compare_native_text
        for index, row in enumerate(self.rows):
            path = self.root / row['rel_path']
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                if index == 0:
                    page.insert_text((72, 72), 'GB 1-2026 clause 1')
                pdf.save(path)
            row['sha256'] = corpus_fixture.digest(path.read_bytes())
        self.fixture.write_inventory(self.rows)
        assets = build_source_catalog(self.root).assets
        result = compare_native_text(self.root, *assets)
        self.assertIsNone(result['normalized_native_text_equal'])
        self.assertIsNone(result['character_5gram_jaccard'])
        self.assertFalse(result['can_confirm_same_version'])
        (self.root / assets[0].canonical_rel_path).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            compare_native_text(self.root, *assets)

    def test_primary_switch_changes_fingerprint_without_inheriting_page_approval(self):
        self.relation()
        service = CorpusUpdateService(self.root, page_extractor=corpus_fixture.FakePageExtractor())
        first = service.build()
        self.relation(primary_sha256=self.rows[1]['sha256'])
        second = service.build()
        self.assertNotEqual(first['corpus_version'], second['corpus_version'])
        self.assertEqual(second['processed_content_count'], 1)
        self.assertNotEqual(first['document_version_ids'], second['document_version_ids'])
        self.assertTrue(all(m['page_mapping_status'] in {'identity', 'not_established'}
                            for m in second['document_relations']['file_mappings']))

    def test_missing_comparison_and_overlapping_confirmation_fail_closed(self):
        self.relation(comparison={})
        with self.assertRaises(ValueError):
            build_source_catalog(self.root)
        self.relation()
        path = self.root / 'data/registry/document_relations.json'
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['relations'].append(dict(payload['relations'][0], document_version_id='another-version'))
        path.write_text(json.dumps(payload), encoding='utf-8')
        with self.assertRaises(ValueError):
            build_source_catalog(self.root)


if __name__ == '__main__':
    unittest.main()
