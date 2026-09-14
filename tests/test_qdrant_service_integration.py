import unittest

from qdrant_client import QdrantClient

from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex


class QdrantServiceIntegrationTest(unittest.TestCase):
    collection_name = "test_m3_exact_dense_service"

    def test_service_exact_dense_preserves_filter_and_evidence_contract(self):
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=self.collection_name,
            vector_size=2,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000007",
                "text": "全国排水政策。",
                "evidence_ids": ["ev_00000000000000000000000000000007"],
                "primary_evidence_id": "ev_00000000000000000000000000000007",
                "document_version_id": "doc_national",
                "evidence_type": "paragraph",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000008",
                "text": "重庆地块项目评价指标。",
                "evidence_ids": ["ev_00000000000000000000000000000008"],
                "primary_evidence_id": "ev_00000000000000000000000000000008",
                "document_version_id": "doc_chongqing",
                "evidence_type": "table",
                "physical_pages": [15],
                "metadata": {"jurisdiction": "重庆", "usage_policy": "answer_and_citation"},
            },
        ]
        try:
            index.build(chunks, [[1.0, 0.0], [0.8, 0.2]])
            hits = index.search(
                [1.0, 0.0],
                mode="exact_dense",
                filters={"jurisdiction": "重庆"},
                limit=2,
            )

            self.assertEqual([hit.chunk_id for hit in hits], [chunks[1]["chunk_id"]])
            self.assertEqual(hits[0].primary_evidence_id, chunks[1]["primary_evidence_id"])
            self.assertEqual(hits[0].physical_pages, [15])
        finally:
            if client.collection_exists(self.collection_name):
                client.delete_collection(self.collection_name)

    def test_service_ann_dense_uses_the_same_result_contract(self):
        collection_name = "test_m3_ann_dense_service"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000015",
                "text": "城镇排水管渠与泵站运行维护。",
                "primary_evidence_id": "ev_00000000000000000000000000000015",
                "physical_pages": [8],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000016",
                "text": "地下水硬度检测。",
                "primary_evidence_id": "ev_00000000000000000000000000000016",
                "physical_pages": [9],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
        ]
        try:
            index.build(chunks, [[1.0, 0.0], [0.0, 1.0]])
            hits = index.search(
                [1.0, 0.0],
                mode="ann_dense",
                filters={"jurisdiction": "全国"},
                limit=2,
            )

            self.assertEqual(hits[0].chunk_id, chunks[0]["chunk_id"])
            self.assertEqual(hits[0].primary_evidence_id, chunks[0]["primary_evidence_id"])
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    def test_service_filters_by_region_kind_status_and_effective_date(self):
        collection_name = "test_m3_governance_filters_service"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
        )

        def chunk(number, *, jurisdiction, kind, status, effective_from, effective_to):
            return {
                "chunk_id": f"chunk_{number:032d}",
                "text": f"治理要求版本{number}。",
                "primary_evidence_id": f"ev_{number:032d}",
                "physical_pages": [number],
                "metadata": {
                    "jurisdiction": jurisdiction,
                    "document_kind": kind,
                    "effective_status": status,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "usage_policy": "answer_and_citation",
                },
            }

        chunks = [
            chunk(21, jurisdiction="重庆", kind="policy", status="current", effective_from="2024-01-01", effective_to="2025-12-31"),
            chunk(22, jurisdiction="重庆", kind="policy", status="current", effective_from="2026-01-01", effective_to="2099-12-31"),
            chunk(23, jurisdiction="全国", kind="policy", status="current", effective_from="2024-01-01", effective_to="2099-12-31"),
            chunk(24, jurisdiction="重庆", kind="standard", status="current", effective_from="2024-01-01", effective_to="2099-12-31"),
            chunk(25, jurisdiction="重庆", kind="policy", status="repealed", effective_from="2024-01-01", effective_to="2099-12-31"),
        ]
        try:
            index.build(chunks, [[0.8, 0.2], [1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.97, 0.03]])
            hits = index.search(
                [1.0, 0.0],
                mode="exact_dense",
                filters={
                    "jurisdiction": "重庆",
                    "document_kind": "policy",
                    "effective_status": "current",
                    "as_of": "2025-06-01",
                },
                limit=5,
            )

            self.assertEqual([hit.chunk_id for hit in hits], [chunks[0]["chunk_id"]])
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    def test_service_multilingual_bm25_prefers_the_numeric_table(self):
        collection_name = "test_m3_multilingual_bm25_service"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
            enable_bm25=True,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000009",
                "text": "降水量按24h、12h两个时间段进行划分。",
                "evidence_ids": ["ev_00000000000000000000000000000009"],
                "primary_evidence_id": "ev_00000000000000000000000000000009",
                "document_version_id": "doc_rainfall",
                "evidence_type": "paragraph",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000010",
                "text": "表1：12h降雨量，大雨15.0~29.9毫米；24h降雨量，中雨10.0~24.9毫米。",
                "evidence_ids": ["ev_00000000000000000000000000000010"],
                "primary_evidence_id": "ev_00000000000000000000000000000010",
                "document_version_id": "doc_rainfall",
                "evidence_type": "table",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
        ]
        try:
            index.build(chunks, [[1.0, 0.0], [0.8, 0.2]])
            hits = index.search(
                "12h降雨量20毫米属于什么等级",
                mode="bm25",
                filters={"jurisdiction": "全国"},
                limit=2,
            )

            self.assertEqual(hits[0].chunk_id, chunks[1]["chunk_id"])
            self.assertEqual(hits[0].primary_evidence_id, chunks[1]["primary_evidence_id"])
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    def test_service_rrf_combines_dense_and_bm25_rankings(self):
        collection_name = "test_m3_rrf_service"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
            enable_bm25=True,
        )
        chunks = [
            {
                "chunk_id": "chunk_00000000000000000000000000000011",
                "text": "城市建设项目的一般说明。",
                "primary_evidence_id": "ev_00000000000000000000000000000011",
                "physical_pages": [3],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000012",
                "text": "表1：12h降雨量，大雨15.0~29.9毫米。",
                "primary_evidence_id": "ev_00000000000000000000000000000012",
                "physical_pages": [4],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
            {
                "chunk_id": "chunk_00000000000000000000000000000013",
                "text": "地下水硬度检测指标。",
                "primary_evidence_id": "ev_00000000000000000000000000000013",
                "physical_pages": [9],
                "metadata": {"jurisdiction": "全国", "usage_policy": "answer_and_citation"},
            },
        ]
        try:
            index.build(chunks, [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
            hits = index.search(
                HybridQuery(
                    text="12h降雨量20毫米属于什么等级",
                    dense_vector=[1.0, 0.0],
                ),
                mode="rrf",
                filters={"jurisdiction": "全国"},
                limit=3,
            )

            self.assertEqual(hits[0].chunk_id, chunks[1]["chunk_id"])
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)


if __name__ == "__main__":
    unittest.main()
