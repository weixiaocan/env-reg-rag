import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack
from src.server import runtime


class FullCorpusRuntimeTest(unittest.TestCase):
    def test_published_source_lookup_uses_current_corpus_and_safety_filter(self):
        self.check('corpus-abc', 'corpus_current')

    def test_legacy_fallback_keeps_existing_locator_projection(self):
        self.check('formal-corpus-v1', 'm3_experiment_current')

    def check(self, version, locator_alias):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            names = ['LlmProviderSettings', 'BgeSmallZhEmbedder', 'QdrantClient',
                     'build_answer_generator', 'NumericRangeIndex', 'QdrantRetrievalIndex',
                     'JsonlQueryTraceRecorder', 'QueryApplicationService',
                     'QdrantEvidenceRetriever', 'InventoryDocumentCatalog',
                     'EvidenceSourceService', 'QdrantEvidenceCatalog', 'QdrantReadinessProbe']
            mocked = {name: stack.enter_context(patch.object(runtime, name)) for name in names}
            stack.enter_context(patch.object(runtime, 'resolve_current_corpus', return_value=SimpleNamespace(
                corpus_version=version, query_alias='corpus_current',
                evidence_units_path=Path(folder)/'evidence.jsonl',
                manifest_path=Path(folder)/'manifest.json')))
            runtime.build_query_services(project_root=Path(folder))
            index_calls = mocked['QdrantRetrievalIndex'].call_args_list
            self.assertEqual(index_calls[1].kwargs['collection_name'], locator_alias)
            retriever = mocked['QdrantEvidenceRetriever'].call_args_list[1]
            self.assertEqual(retriever.kwargs['fixed_filters'], {'usage_policy': 'source_locator_only'})
            answer = mocked['QdrantEvidenceRetriever'].call_args_list[0]
            self.assertEqual(answer.kwargs['fixed_filters'], {'usage_policy': 'answer_and_citation'})
            requirements = mocked['QdrantReadinessProbe'].call_args.kwargs['required_collections']
            self.assertEqual(requirements, list(dict.fromkeys(['corpus_current', locator_alias])))
