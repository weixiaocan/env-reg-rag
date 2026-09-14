import unittest

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from src.adapters.qdrant_evidence_catalog import QdrantEvidenceCatalog
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.evidence_source import EvidenceSourceService
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.server.fastapi_app import create_app


class EvidenceHttpApiTest(unittest.TestCase):
    def test_user_can_fetch_cited_evidence_by_its_stable_id(self):
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        formal_name = "test_m5_evidence_catalog_formal"
        experiment_name = "test_m5_evidence_catalog_experiment"
        formal = QdrantRetrievalIndex(
            client=client,
            collection_name=formal_name,
            vector_size=2,
            enable_bm25=True,
        )
        experiment = QdrantRetrievalIndex(
            client=client,
            collection_name=experiment_name,
            vector_size=2,
            enable_bm25=True,
        )
        evidence_id = "ev_00000000000000000000000000000061"
        chunk = {
            "chunk_id": "chunk_00000000000000000000000000000061",
            "text": "原文：12小时降水量15.0~29.9毫米为大雨。",
            "primary_evidence_id": evidence_id,
            "document_version_id": "doc_rainfall",
            "physical_pages": [4],
            "metadata": {
                "file_name": "降水量等级.pdf",
                "heading_path": ["表1"],
                "usage_policy": "answer_and_citation",
                "jurisdiction": "全国",
                "document_kind": "standard",
                "effective_status": "current",
                "official_source_uri": "https://example.org/rainfall.pdf",
            },
        }
        try:
            formal.build([chunk], [[1.0, 0.0]])
            experiment.build([], [])
            http = TestClient(
                create_app(
                    query_service=self._query_service(),
                    evidence_catalog=EvidenceSourceService(
                        QdrantEvidenceCatalog(
                            formal_index=formal,
                            source_locator_index=experiment,
                        )
                    ),
                )
            )

            response = http.get(f"/api/v1/evidence/{evidence_id}")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["evidence_id"], evidence_id)
            self.assertEqual(body["document_version_id"], "doc_rainfall")
            self.assertEqual(body["physical_pages"], [4])
            self.assertEqual(body["heading_path"], ["表1"])
            self.assertEqual(body["usage_policy"], "answer_and_citation")
        finally:
            for name in (formal_name, experiment_name):
                if client.collection_exists(name):
                    client.delete_collection(name)

    @staticmethod
    def _query_service() -> QueryApplicationService:
        return QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )


if __name__ == "__main__":
    unittest.main()
