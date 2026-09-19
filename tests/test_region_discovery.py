import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from src.ingestion.region_discovery import discover_regions
from scripts.discover_pdf_regions import scan_published


class RegionDiscoveryTests(unittest.TestCase):
    def document(self, elements):
        return {'sha256': 'a' * 64, 'file_name': 'sample.pdf',
                'pages': [{'physical_page': 1, 'width': 100, 'height': 100,
                           'elements': elements, 'tables': []}]}

    def test_signals_and_deduplication(self):
        report = discover_regions([self.document([
            {'element_id': 'normal', 'text': 'GB 50014-2021 第3条'},
            {'element_id': 'eq', 'type': 'formula', 'text': 'Q=A×v', 'bbox': [1, 2, 30, 20]},
            {'element_id': 'caption', 'text': '表 3.1 参数表', 'bbox': [1, 30, 50, 40]},
        ])])
        self.assertEqual(len(report['candidates']), 2)
        self.assertEqual(report['candidates'][0]['kind'], 'formula')
        self.assertEqual(report['candidates'][1]['kind'], 'table_caption')
        self.assertFalse(report['candidates'][0]['publishable'])
        self.assertIn('typed_element', report['candidates'][0]['reasons'])

    def test_invalid_coordinates_are_unknown_not_discarded(self):
        report = discover_regions([self.document([
            {'element_id': 'eq', 'text': 'Q=A×v', 'bbox': [0, 0, 999, 20]}
        ])])
        self.assertIsNone(report['candidates'][0]['bbox'])
        self.assertEqual(report['candidates'][0]['location_status'], 'unknown')

    def test_empty_page_is_not_clean(self):
        report = discover_regions([self.document([])])
        self.assertEqual(report['pages_without_text'], 1)
        self.assertEqual(report['candidates'], [])
        self.assertFalse(report['complete_structure_coverage'])

    def test_stable_identity(self):
        doc = self.document([{'element_id': 'a', 'text': '按下列公式计算'}])
        self.assertEqual(discover_regions([doc]), discover_regions([doc]))

    def test_manifest_integrity_and_physical_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'data/canonical/docs.jsonl'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.document([{'text': '计算公式\u2028说明'}]), ensure_ascii=False) + '\n', encoding='utf-8')
            manifest_path = root / 'manifest.json'
            manifest = {'canonical_documents': 'data/canonical/docs.jsonl',
                        'canonical_documents_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'page_count': 1, 'selected_content_count': 1}
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            current = SimpleNamespace(manifest_path=manifest_path, corpus_version='corpus-test')
            with patch('scripts.discover_pdf_regions.resolve_current_corpus', return_value=current):
                self.assertEqual(scan_published(root)['candidate_count'], 1)
                manifest['page_count'] = 2
                manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'page count'):
                    scan_published(root)
                path.write_text('{}\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    scan_published(root)

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps({'canonical_documents': '../outside.jsonl'}), encoding='utf-8')
            current = SimpleNamespace(manifest_path=manifest_path, corpus_version='corpus-test')
            with patch('scripts.discover_pdf_regions.resolve_current_corpus', return_value=current):
                with self.assertRaisesRegex(ValueError, 'outside'):
                    scan_published(root)


if __name__ == '__main__':
    unittest.main()
