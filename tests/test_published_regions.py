import hashlib
import json
import unittest
from unittest.mock import Mock
from fastapi.testclient import TestClient
from src.server.fastapi_app import create_app
from src.application.published_regions import PublishedRegionService
from tests.test_unified_pdf import UnifiedPdfTests


class PublishedRegionTests(unittest.TestCase):
    def setUp(self):
        fixture = UnifiedPdfTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root
        page = fixture.extractor().extract(fixture.asset, physical_page=1)
        self.region = next(r for r in page['regions'] if r['kind'] == 'image')
        canonical = self.root / 'data/canonical/source.jsonl'
        canonical.parent.mkdir(parents=True)
        document = {'sha256': fixture.sha, 'canonical_rel_path': fixture.asset.canonical_rel_path, 'pages': [page]}
        canonical.write_text(json.dumps(document), encoding='utf-8')
        registry = self.root / 'data/registry'
        registry.mkdir(parents=True)
        manifest = {'corpus_version': 'corpus-fixture', 'canonical_documents': 'data/canonical/source.jsonl',
                    'canonical_documents_sha256': hashlib.sha256(canonical.read_bytes()).hexdigest()}
        (registry / 'fixture.json').write_text(json.dumps(manifest), encoding='utf-8')
        (registry / 'corpus-current.json').write_text(json.dumps({'manifest': 'data/registry/fixture.json',
                                                               'corpus_version': 'corpus-fixture'}), encoding='utf-8')

    def test_published_source_image_and_identity(self):
        service = PublishedRegionService(self.root)
        item = service.get(self.region['region_id'])
        self.assertEqual(item['file_sha256'], self.fixture.sha)
        self.assertEqual(item['physical_page'], 1)
        self.assertEqual(item['usage_policy'], 'source_locator_only')
        self.assertTrue(service.image(item['region_id']).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_http_detail_original_and_no_path_input(self):
        service = PublishedRegionService(self.root)
        client = TestClient(create_app(query_service=Mock(), published_regions=service))
        url = '/api/v1/regions/' + self.region['region_id']
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['corpus_version'], 'corpus-fixture')
        image = client.get(url + '/image')
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers['content-type'], 'image/png')
        self.assertEqual(image.headers['cache-control'], 'no-store')
        self.assertEqual(client.get('/api/v1/regions/unknown/image').status_code, 404)

    def test_source_change_removes_original_and_excerpts(self):
        service = PublishedRegionService(self.root)
        with (self.root / self.fixture.asset.canonical_rel_path).open('ab') as f:
            f.write(b'changed')
        self.assertIsNone(service.get(self.region['region_id']))
        self.assertIsNone(service.image(self.region['region_id']))

    def test_unicode_separator_is_content_not_a_jsonl_boundary(self):
        canonical = self.root / 'data/canonical/source.jsonl'
        document = json.loads(canonical.read_text(encoding='utf-8'))
        document['metadata'] = {'original_text': 'first\u2028second'}
        canonical.write_text(json.dumps(document, ensure_ascii=False)+'\n', encoding='utf-8')
        manifest_path = self.root / 'data/registry/fixture.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['canonical_documents_sha256'] = hashlib.sha256(canonical.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        self.assertIsNotNone(PublishedRegionService(self.root).get(self.region['region_id']))

    def test_canonical_tamper_rejected_before_exposing_regions(self):
        with (self.root / 'data/canonical/source.jsonl').open('a', encoding='utf-8') as f:
            f.write(' ')
        with self.assertRaises(ValueError):
            PublishedRegionService(self.root)
