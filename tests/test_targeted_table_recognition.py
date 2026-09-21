import unittest
from scripts.recognize_pdf_tables import recognize
from tests import _pdf_fixture as corpus_fixture


class Parser:
    def __init__(self, failure=False):
        self.calls = []
        self.failure = failure

    def parse_page(self, path, *, physical_page):
        self.calls.append(physical_page)
        if self.failure:
            raise RuntimeError('sensitive provider detail must not be serialized')
        return {'physical_page': physical_page, 'width': 100, 'height': 100,
                'raw': {'render_width': 200, 'render_height': 200, 'engine_name': 'fixture',
                        'engine_version': '1', 'render_dpi': 200},
                'tables': [{'html': '<tr><td>≥8.0mg/L</td></tr>',
                            'raw': {'cell_box_list': [[20, 40, 180, 100]]}}]}


class TargetedTableRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = corpus_fixture.CorpusUpdateTest()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.row = self.fixture.row(name='table.pdf', rel='data/raw/资料/table.pdf', data=b'fixture')
        self.fixture.write_inventory([self.row])

    def tearDown(self):
        self.fixture.tearDown()

    def test_selected_page_is_recognized_without_native_text_shortcut(self):
        parser = Parser()
        result = recognize(self.root, self.row['sha256'], 1, parser=parser)
        self.assertEqual(parser.calls, [1])
        self.assertEqual(result['status'], 'pending_review')
        self.assertFalse(result['publishable'])
        self.assertEqual(result['tables'][0]['cells'][0]['text'], '≥8.0mg/L')
        self.assertFalse((self.root / 'data/registry/corpus-current.json').exists())

    def test_engine_error_preserves_source_location_without_sensitive_message(self):
        result = recognize(self.root, self.row['sha256'], 1, parser=Parser(failure=True))
        self.assertEqual(result['status'], 'source_page_only')
        self.assertEqual(result['failure'], 'RuntimeError')
        self.assertEqual(result['file_sha256'], self.row['sha256'])
        self.assertNotIn('sensitive provider', str(result))

    def test_unknown_hash_invalid_page_and_changed_bytes_fail_closed(self):
        for digest, page in [('0'*64, 1), (self.row['sha256'], 0),
                             (self.row['sha256'], 2), (self.row['sha256'], True)]:
            with self.assertRaises(ValueError):
                recognize(self.root, digest, page, parser=Parser())
        (self.root / self.row['rel_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            recognize(self.root, self.row['sha256'], 1, parser=Parser())


if __name__ == '__main__':
    unittest.main()
