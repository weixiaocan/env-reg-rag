import unittest

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from qdrant_client import QdrantClient

from src.adapters.langchain_answer_generator import LangChainAnswerGenerator
from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import QueryRequest, QueryStatus
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex


class _KnownQueryEmbedder:
    def embed_query(self, text):
        return [1.0, 0.0]


class QdrantEvidenceQueryTest(unittest.IsolatedAsyncioTestCase):
    async def test_source_lookup_never_uses_citable_experiment_evidence_to_answer(self):
        collection_name = "test_m5_source_locator_filter"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
            enable_bm25=True,
        )
        locator_id = "ev_00000000000000000000000000000051"
        locator_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000051",
            "text": "标题：原文定位\n原文：变量说明。5.2.4采用用水量折算法。",
            "primary_evidence_id": locator_id,
            "document_version_id": "doc_formula_locator",
            "physical_pages": [19],
            "metadata": {
                "jurisdiction": "全国",
                "file_name": "入流入渗监测标准.pdf",
                "heading_path": [],
                "usage_policy": "source_locator_only",
            },
        }
        citable_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000052",
            "text": "用水量与排水量的一般说明。",
            "primary_evidence_id": "ev_00000000000000000000000000000052",
            "document_version_id": "doc_experiment_citable",
            "physical_pages": [8],
            "metadata": {
                "jurisdiction": "全国",
                "usage_policy": "answer_and_citation",
            },
        }
        try:
            index.build([locator_chunk, citable_chunk], [[0.9, 0.1], [1.0, 0.0]])
            service = QueryApplicationService(
                scope_resolver=RuleBasedScopeResolver(),
                evidence_retriever=QdrantEvidenceRetriever(
                    index=index,
                    embedder=_KnownQueryEmbedder(),
                    fixed_filters={"usage_policy": "source_locator_only"},
                    limit=5,
                ),
                answer_generator=InMemoryAnswerGenerator(None),
            )

            result = await service.execute(
                QueryRequest(
                    request_id="query-source-only-001",
                    question="采用用水量折算法时，原文公式在哪里？",
                    conversation_context={},
                    corpus_version="m3-experiment-v1",
                    caller_type="web",
                )
            )

            self.assertEqual(result.status, QueryStatus.SEARCH_ONLY)
            self.assertIsNone(result.answer)
            self.assertEqual([item.evidence_id for item in result.evidence], [locator_id])
            self.assertEqual(result.evidence[0].physical_pages, [19])
            self.assertEqual(result.evidence[0].heading_path, ["5.2.4"])
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    async def test_query_service_turns_qdrant_hit_into_citable_evidence(self):
        collection_name = "test_m4_qdrant_evidence_query"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
            enable_bm25=True,
        )
        local_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000031",
            "text": (
                "标准号：DB4201/T 651-2021\n"
                "文档：武汉市排水管道混错接改造技术规程.pdf\n"
                "原文：8.3.1 评估周期应至少跨越一个完整雨季。"
            ),
            "primary_evidence_id": "ev_00000000000000000000000000000031",
            "document_version_id": "doc_wuhan_test",
            "physical_pages": [34],
            "metadata": {
                "jurisdiction": "武汉",
                "document_kind": "standard",
                "effective_status": "current",
                "file_name": "武汉市排水管道混错接改造技术规程.pdf",
                "heading_path": [],
                "usage_policy": "answer_and_citation",
                "official_source_uri": "https://swj.wuhan.gov.cn/official.pdf",
            },
        }
        national_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000032",
            "text": "国家文件对同类评估给出通用要求。",
            "primary_evidence_id": "ev_00000000000000000000000000000032",
            "document_version_id": "doc_national_test",
            "physical_pages": [9],
            "metadata": {
                "jurisdiction": "全国",
                "document_kind": "standard",
                "effective_status": "current",
                "file_name": "国家通用要求.pdf",
                "heading_path": ["4.1"],
                "usage_policy": "answer_and_citation",
                "official_source_uri": "https://example.org/national.pdf",
            },
        }
        try:
            index.build(
                [local_chunk, national_chunk],
                [[1.0, 0.0], [0.9, 0.1]],
            )
            service = QueryApplicationService(
                scope_resolver=RuleBasedScopeResolver(),
                evidence_retriever=QdrantEvidenceRetriever(
                    index=index,
                    embedder=_KnownQueryEmbedder(),
                    limit=5,
                ),
                answer_generator=LangChainAnswerGenerator(
                    model=FakeListChatModel(
                        responses=[
                            '{"claims":[{"claim_id":"claim-1",'
                            '"text":"评估周期应至少跨越一个完整雨季。",'
                            '"evidence_ids":['
                            '"ev_00000000000000000000000000000031"]}]}'
                        ]
                    )
                ),
            )
            result = await service.execute(
                QueryRequest(
                    request_id="query-qdrant-evidence-001",
                    question="武汉市混错接改造项目的评估周期有什么要求？",
                    conversation_context={"jurisdiction": "武汉"},
                    corpus_version="formal-corpus-v1",
                    caller_type="web",
                )
            )

            self.assertEqual(result.status, QueryStatus.ANSWERED)
            self.assertEqual(result.evidence[0].document_version_id, "doc_wuhan_test")
            self.assertEqual(result.evidence[0].physical_pages, [34])
            self.assertEqual(result.evidence[0].heading_path, ["8.3.1"])
            self.assertEqual(
                [item.jurisdiction for item in result.evidence],
                ["武汉", "全国"],
            )
            self.assertEqual(result.evidence[0].effective_status, "current")
            self.assertEqual(
                result.evidence[0].source_uri,
                "https://swj.wuhan.gov.cn/official.pdf",
            )
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)

    async def test_numeric_interval_query_promotes_the_matching_table_evidence(self):
        collection_name = "test_m4_numeric_interval_query"
        client = QdrantClient(url="http://127.0.0.1:6333", timeout=5)
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=2,
            enable_bm25=True,
        )
        table_evidence_id = "ev_00000000000000000000000000000041"
        table_text = (
            "等 级 | 时段降雨量\n"
            "12h降雨量 | 24h降雨量\n"
            "中雨 | 5.0~14.9 | 10.0~24.9\n"
            "大雨 | 15.0~29.9 | 25.0~49.9"
        )
        table_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000041",
            "text": f"标题路径：表1不同时段的降雨量等级划分表\n原文：{table_text}",
            "primary_evidence_id": table_evidence_id,
            "document_version_id": "doc_rainfall",
            "physical_pages": [4],
            "metadata": {
                "jurisdiction": "全国",
                "document_kind": "standard",
                "effective_status": "current",
                "file_name": "降水量等级.pdf",
                "heading_path": ["表1不同时段的降雨量等级划分表"],
                "usage_policy": "answer_and_citation",
            },
        }
        distractor_chunk = {
            "chunk_id": "chunk_00000000000000000000000000000042",
            "text": "20毫米管径的一般要求。",
            "primary_evidence_id": "ev_00000000000000000000000000000042",
            "document_version_id": "doc_distractor",
            "physical_pages": [8],
            "metadata": {
                "jurisdiction": "全国",
                "usage_policy": "answer_and_citation",
            },
        }
        numeric_index = NumericRangeIndex.from_evidence_units(
            [
                {
                    "evidence_id": table_evidence_id,
                    "document_version_id": "doc_rainfall",
                    "evidence_type": "table",
                    "quality_status": "approved",
                    "usage_policy": "answer_and_citation",
                    "title": "表1不同时段的降雨量等级划分表",
                    "text": table_text,
                }
            ]
        )
        try:
            index.build(
                [table_chunk, distractor_chunk],
                [[0.0, 1.0], [1.0, 0.0]],
            )
            service = QueryApplicationService(
                scope_resolver=RuleBasedScopeResolver(),
                evidence_retriever=QdrantEvidenceRetriever(
                    index=index,
                    embedder=_KnownQueryEmbedder(),
                    numeric_range_index=numeric_index,
                    limit=1,
                ),
                answer_generator=LangChainAnswerGenerator(
                    model=FakeListChatModel(
                        responses=[
                            '{"claims":[{"claim_id":"claim-rainfall",'
                            '"text":"按12小时统计，20毫米属于大雨。",'
                            f'"evidence_ids":["{table_evidence_id}"]}}]}}'
                        ]
                    )
                ),
            )

            result = await service.execute(
                QueryRequest(
                    request_id="query-numeric-range-001",
                    question="12小时降水量为20毫米时属于什么等级？",
                    conversation_context={},
                    corpus_version="formal-corpus-v1",
                    caller_type="web",
                )
            )

            self.assertEqual(result.status, QueryStatus.ANSWERED)
            self.assertEqual(result.evidence[0].evidence_id, table_evidence_id)
            self.assertEqual(result.answer.claims[0].text, "按12小时统计，20毫米属于大雨。")
        finally:
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)


if __name__ == "__main__":
    unittest.main()
