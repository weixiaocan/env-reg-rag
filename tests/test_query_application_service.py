import unittest

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.adapters.langchain_answer_generator import (
    AnswerGenerationError,
    LangChainAnswerGenerator,
)
from src.application.query_service import CitationValidationError, QueryApplicationService
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import (
    Answer,
    Claim,
    Conflict,
    EvidenceItem,
    EvidencePack,
    QueryRequest,
    QueryStatus,
)


class _QuestionBoundEvidenceRetriever:
    """Retrieval boundary fake that only recognizes the complete effective question."""

    def __init__(self, *, expected_question, evidence):
        self._expected_question = expected_question
        self._evidence = evidence

    async def retrieve(self, *, question, resolved_scope, corpus_version):
        evidence = self._evidence if question == self._expected_question else []
        return EvidencePack(corpus_version=corpus_version, evidence=evidence)


class QueryApplicationServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_rainfall_period_returns_structured_clarification(self):
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        request = QueryRequest(
            request_id="query-real-001",
            question="雨量为20毫米时，属于小雨、中雨还是大雨？",
            conversation_context={},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.query_id, "query-real-001")
        self.assertEqual(result.status, QueryStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.missing_conditions, ["statistical_period"])
        self.assertIsNone(result.answer)
        self.assertEqual(result.evidence, [])
        self.assertEqual(result.corpus_version, "formal-corpus-v1")

    async def test_user_can_supply_the_missing_period_in_the_next_query(self):
        original_question = "雨量为20毫米时，属于小雨、中雨还是大雨？"
        effective_question = f"{original_question}（统计时段：12h）"
        evidence = EvidenceItem(
            evidence_id="ev_rainfall_12h_20mm",
            text="12小时降水量15.0至29.9毫米为大雨。",
            document_version_id="doc_rainfall",
            file_name="降水量等级.pdf",
            physical_pages=[4],
            heading_path=["表1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/rainfall.pdf",
        )
        answer = Answer(
            claims=[
                Claim(
                    claim_id="claim-rainfall",
                    text="按12小时统计，20毫米属于大雨。",
                    evidence_ids=[evidence.evidence_id],
                )
            ]
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=_QuestionBoundEvidenceRetriever(
                expected_question=effective_question,
                evidence=[evidence],
            ),
            answer_generator=InMemoryAnswerGenerator(answer),
        )

        first = await service.execute(
            QueryRequest(
                request_id="query-rainfall-turn-1",
                question=original_question,
                conversation_context={},
                corpus_version="formal-corpus-v1",
                caller_type="web",
            )
        )
        second = await service.execute(
            QueryRequest(
                request_id="query-rainfall-turn-2",
                question="12小时",
                conversation_context=first.continuation_context,
                corpus_version="formal-corpus-v1",
                caller_type="web",
            )
        )

        self.assertEqual(
            first.continuation_context,
            {"pending_question": original_question},
        )
        self.assertEqual(second.status, QueryStatus.ANSWERED)
        self.assertEqual(second.resolved_scope, {"statistical_period": "12h"})
        self.assertEqual(second.answer, answer)
        self.assertEqual(
            second.continuation_context,
            {
                "anchor_question": original_question,
                "statistical_period": "12h",
            },
        )

    async def test_locator_only_formula_evidence_returns_search_only(self):
        locator = EvidenceItem(
            evidence_id="ev_58337104c115f4ddb491ad736f859ae7",
            text="5.2.4 用水量折算法公式位于本页，自动转写未经批准。",
            document_version_id="doc_formula_source",
            file_name="入流入渗监测与评估标准.pdf",
            physical_pages=[19],
            heading_path=["5.2.4"],
            usage_policy="source_locator_only",
            source_uri="https://example.org/official.pdf",
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([locator]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        request = QueryRequest(
            request_id="query-formula-001",
            question="用水量折算法计算旱天入流入渗量的公式是什么？",
            conversation_context={},
            corpus_version="experiment-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.status, QueryStatus.SEARCH_ONLY)
        self.assertIsNone(result.answer)
        self.assertEqual(result.evidence, [locator])
        self.assertEqual(result.evidence[0].physical_pages, [19])
        self.assertEqual(result.evidence[0].heading_path, ["5.2.4"])

    async def test_answered_claim_is_bound_to_citable_evidence(self):
        evidence = EvidenceItem(
            evidence_id="ev_d6a4f70024ff1539091b28cdf9a66bf3",
            text="评估周期应至少跨越一个完整雨季。",
            document_version_id="doc_48429f3750209858",
            file_name="武汉市排水管道混错接改造技术规程.pdf",
            physical_pages=[34],
            heading_path=["评估周期"],
            usage_policy="answer_and_citation",
            source_uri="https://swj.wuhan.gov.cn/official.pdf",
        )
        draft = Answer(
            claims=[
                Claim(
                    claim_id="claim-1",
                    text="武汉市混错接改造项目的评估周期应至少跨越一个完整雨季。",
                    evidence_ids=[evidence.evidence_id],
                )
            ]
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=InMemoryAnswerGenerator(draft),
        )
        request = QueryRequest(
            request_id="query-wuhan-001",
            question="武汉市混错接改造项目的评估周期有什么要求？",
            conversation_context={"jurisdiction": "武汉"},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.status, QueryStatus.ANSWERED)
        self.assertEqual(result.answer, draft)
        self.assertEqual(result.answer.claims[0].evidence_ids, [evidence.evidence_id])
        self.assertEqual(result.evidence, [evidence])

    async def test_openai_compatible_model_generates_a_citation_bound_answer(self):
        evidence = EvidenceItem(
            evidence_id="ev_d6a4f70024ff1539091b28cdf9a66bf3",
            text="评估周期应至少跨越一个完整雨季。",
            document_version_id="doc_48429f3750209858",
            file_name="武汉市排水管道混错接改造技术规程.pdf",
            physical_pages=[34],
            heading_path=["评估周期"],
            usage_policy="answer_and_citation",
            source_uri="https://swj.wuhan.gov.cn/official.pdf",
        )
        model = FakeListChatModel(
            responses=[
                '{"claims":[{"claim_id":"claim-1",'
                '"text":"评估周期应至少跨越一个完整雨季。",'
                '"evidence_ids":["ev_d6a4f70024ff1539091b28cdf9a66bf3"]}]}'
            ]
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=LangChainAnswerGenerator(model=model),
        )
        request = QueryRequest(
            request_id="query-langchain-001",
            question="武汉市混错接改造项目的评估周期有什么要求？",
            conversation_context={"jurisdiction": "武汉"},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.status, QueryStatus.ANSWERED)
        self.assertEqual(result.answer.claims[0].text, "评估周期应至少跨越一个完整雨季。")
        self.assertEqual(result.answer.claims[0].evidence_ids, [evidence.evidence_id])

    async def test_invalid_model_json_is_a_visible_generation_error(self):
        evidence = EvidenceItem(
            evidence_id="ev_parse_error_test",
            text="本证据只用于验证结构化输出失败路径。",
            document_version_id="doc_parse_error_test",
            file_name="测试证据.pdf",
            physical_pages=[1],
            heading_path=["1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/test.pdf",
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=LangChainAnswerGenerator(
                model=FakeListChatModel(responses=["这不是 JSON"])
            ),
        )
        request = QueryRequest(
            request_id="query-invalid-json",
            question="这份测试证据说了什么？",
            conversation_context={},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        with self.assertRaises(AnswerGenerationError) as raised:
            await service.execute(request)

        self.assertEqual(raised.exception.raw_output, "这不是 JSON")

    async def test_irrelevant_retrieval_results_become_a_controlled_refusal(self):
        evidence = EvidenceItem(
            evidence_id="ev_empty_answer_test",
            text="本条只规定降雨量等级，与区域站点比例无关。",
            document_version_id="doc_empty_answer_test",
            file_name="测试证据.pdf",
            physical_pages=[1],
            heading_path=["1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/test.pdf",
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=LangChainAnswerGenerator(
                model=FakeListChatModel(responses=['{"claims":[]}'])
            ),
        )
        request = QueryRequest(
            request_id="query-empty-answer",
            question="区域日降雨等级如何根据站点比例划分？",
            conversation_context={},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.status, QueryStatus.REFUSED)
        self.assertIsNone(result.answer)
        self.assertEqual(result.evidence, [evidence])
        self.assertIn("retrieved evidence does not support an answer", result.warnings)

    async def test_claim_cannot_cite_locator_only_evidence(self):
        citable = EvidenceItem(
            evidence_id="ev_00000000000000000000000000000041",
            text="可引用的条款正文。",
            document_version_id="doc_current",
            file_name="现行规范.pdf",
            physical_pages=[5],
            heading_path=["5.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/current.pdf",
        )
        locator = EvidenceItem(
            evidence_id="ev_00000000000000000000000000000042",
            text="公式位于本页，转写未经批准。",
            document_version_id="doc_formula",
            file_name="公式规范.pdf",
            physical_pages=[19],
            heading_path=["5.2.4"],
            usage_policy="source_locator_only",
            source_uri="https://example.org/formula.pdf",
        )
        invalid_draft = Answer(
            claims=[
                Claim(
                    claim_id="claim-invalid",
                    text="公式等于某个未经核验的表达式。",
                    evidence_ids=[locator.evidence_id],
                )
            ]
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([citable, locator]),
            answer_generator=InMemoryAnswerGenerator(invalid_draft),
        )
        request = QueryRequest(
            request_id="query-invalid-citation",
            question="请说明公式及相关条款。",
            conversation_context={},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        with self.assertRaises(CitationValidationError):
            await service.execute(request)

    async def test_incompatible_requirements_from_two_documents_return_conflict(self):
        local_evidence = EvidenceItem(
            evidence_id="ev_conflict_local",
            text="本地文件要求监测周期至少为两个完整雨季。",
            document_version_id="doc_local",
            file_name="地方要求.pdf",
            physical_pages=[8],
            heading_path=["5.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/local.pdf",
        )
        national_evidence = EvidenceItem(
            evidence_id="ev_conflict_national",
            text="国家文件要求监测周期为一个完整雨季。",
            document_version_id="doc_national",
            file_name="国家要求.pdf",
            physical_pages=[12],
            heading_path=["6.2"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/national.pdf",
        )
        draft = Answer(
            claims=[
                Claim(
                    claim_id="claim-local",
                    text="地方文件要求至少两个完整雨季。",
                    evidence_ids=[local_evidence.evidence_id],
                ),
                Claim(
                    claim_id="claim-national",
                    text="国家文件要求一个完整雨季。",
                    evidence_ids=[national_evidence.evidence_id],
                ),
            ],
            conflicts=[
                Conflict(
                    conflict_id="conflict-1",
                    description="两份文件规定的最短监测周期不同。",
                    evidence_ids=[
                        local_evidence.evidence_id,
                        national_evidence.evidence_id,
                    ],
                )
            ],
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever(
                [local_evidence, national_evidence]
            ),
            answer_generator=InMemoryAnswerGenerator(draft),
        )

        result = await service.execute(
            QueryRequest(
                request_id="query-conflict-001",
                question="监测周期最少需要多久？",
                conversation_context={"jurisdiction": "测试地区"},
                corpus_version="formal-corpus-v1",
                caller_type="web",
            )
        )

        self.assertEqual(result.status, QueryStatus.CONFLICTING_SOURCES)
        self.assertEqual(result.answer, draft)
        self.assertEqual(result.evidence, [local_evidence, national_evidence])

    async def test_langchain_model_can_return_a_structured_source_conflict(self):
        first = EvidenceItem(
            evidence_id="ev_model_conflict_1",
            text="文件甲要求一个完整雨季。",
            document_version_id="doc_model_conflict_1",
            file_name="文件甲.pdf",
            physical_pages=[1],
            heading_path=["1.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/a.pdf",
        )
        second = EvidenceItem(
            evidence_id="ev_model_conflict_2",
            text="文件乙要求两个完整雨季。",
            document_version_id="doc_model_conflict_2",
            file_name="文件乙.pdf",
            physical_pages=[2],
            heading_path=["2.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/b.pdf",
        )
        model_output = (
            '{"claims":['
            '{"claim_id":"claim-a","text":"文件甲要求一个完整雨季。",'
            '"evidence_ids":["ev_model_conflict_1"]},'
            '{"claim_id":"claim-b","text":"文件乙要求两个完整雨季。",'
            '"evidence_ids":["ev_model_conflict_2"]}],'
            '"conflicts":[{"conflict_id":"conflict-1",'
            '"description":"最短周期要求不同。",'
            '"evidence_ids":["ev_model_conflict_1","ev_model_conflict_2"]}]}'
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([first, second]),
            answer_generator=LangChainAnswerGenerator(
                model=FakeListChatModel(responses=[model_output])
            ),
        )

        result = await service.execute(
            QueryRequest(
                request_id="query-model-conflict",
                question="两份文件的周期要求一致吗？",
                conversation_context={},
                corpus_version="formal-corpus-v1",
                caller_type="web",
            )
        )

        self.assertEqual(result.status, QueryStatus.CONFLICTING_SOURCES)
        self.assertEqual(result.answer.conflicts[0].conflict_id, "conflict-1")

    async def test_conflict_requires_a_claim_for_each_conflicting_source(self):
        first = EvidenceItem(
            evidence_id="ev_unexplained_conflict_1",
            text="文件甲要求一个完整雨季。",
            document_version_id="doc_unexplained_conflict_1",
            file_name="文件甲.pdf",
            physical_pages=[1],
            heading_path=["1.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/a.pdf",
        )
        second = EvidenceItem(
            evidence_id="ev_unexplained_conflict_2",
            text="文件乙要求两个完整雨季。",
            document_version_id="doc_unexplained_conflict_2",
            file_name="文件乙.pdf",
            physical_pages=[2],
            heading_path=["2.1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/b.pdf",
        )
        incomplete_draft = Answer(
            claims=[
                Claim(
                    claim_id="claim-only-a",
                    text="文件甲要求一个完整雨季。",
                    evidence_ids=[first.evidence_id],
                )
            ],
            conflicts=[
                Conflict(
                    conflict_id="conflict-incomplete",
                    description="两份文件的周期要求不同。",
                    evidence_ids=[first.evidence_id, second.evidence_id],
                )
            ],
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([first, second]),
            answer_generator=InMemoryAnswerGenerator(incomplete_draft),
        )

        with self.assertRaises(CitationValidationError):
            await service.execute(
                QueryRequest(
                    request_id="query-incomplete-conflict",
                    question="两份文件的要求一致吗？",
                    conversation_context={},
                    corpus_version="formal-corpus-v1",
                    caller_type="web",
                )
            )

    async def test_empty_evidence_returns_controlled_refusal(self):
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        request = QueryRequest(
            request_id="query-shanxi-gap",
            question="山西省关于雨污混接治理要求开展哪些工作？",
            conversation_context={"jurisdiction": "山西"},
            corpus_version="formal-corpus-v1",
            caller_type="web",
        )

        result = await service.execute(request)

        self.assertEqual(result.status, QueryStatus.REFUSED)
        self.assertIsNone(result.answer)
        self.assertEqual(result.evidence, [])
        self.assertIn("no evidence in the selected corpus", result.warnings)


if __name__ == "__main__":
    unittest.main()
