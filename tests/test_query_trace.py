import unittest

from src.application.answer_generation import (
    AnswerGenerationResult,
    InMemoryAnswerGenerator,
    ModelUsage,
)
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_observability import InMemoryQueryTraceRecorder
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import Answer, Claim, EvidenceItem, QueryRequest, QueryStatus


class QueryTraceTest(unittest.IsolatedAsyncioTestCase):
    async def test_completed_query_records_stage_timing_usage_and_evidence(self):
        evidence = EvidenceItem(
            evidence_id="ev_trace_table",
            text="12小时降水量15.0~29.9毫米为大雨。",
            document_version_id="doc_rainfall",
            file_name="降水量等级.pdf",
            physical_pages=[4],
            heading_path=["表1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/rainfall.pdf",
        )
        recorder = InMemoryQueryTraceRecorder()
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=InMemoryAnswerGenerator(
                AnswerGenerationResult(
                    answer=Answer(
                        claims=[
                            Claim(
                                claim_id="claim-1",
                                text="12小时20毫米属于大雨。",
                                evidence_ids=[evidence.evidence_id],
                            )
                        ]
                    ),
                    usage=ModelUsage(
                        input_tokens=120,
                        output_tokens=30,
                        total_tokens=150,
                        estimated_cost=0.00042,
                        currency="CNY",
                        pricing_status="calculated",
                    ),
                )
            ),
            trace_recorder=recorder,
            runtime_metadata={
                "provider": "test-provider",
                "model": "test-model",
                "retrieval_profile": "test-rrf",
            },
        )

        result = await service.execute(
            QueryRequest(
                request_id="query-trace-001",
                question="12小时降水量20毫米属于什么等级？",
                conversation_context={},
                corpus_version="formal-corpus-v1",
                caller_type="test",
            )
        )

        self.assertEqual(result.status, QueryStatus.ANSWERED)
        self.assertEqual(len(recorder.traces), 1)
        trace = recorder.traces[0]
        self.assertEqual(trace.query_id, "query-trace-001")
        self.assertEqual(trace.status, "answered")
        self.assertEqual(trace.corpus_version, "formal-corpus-v1")
        self.assertEqual(trace.evidence_ids, [evidence.evidence_id])
        self.assertEqual(
            trace.claim_evidence,
            {"claim-1": [evidence.evidence_id]},
        )
        self.assertEqual(trace.model_usage.total_tokens, 150)
        self.assertEqual(trace.model_usage.estimated_cost, 0.00042)
        self.assertEqual(trace.runtime_metadata["model"], "test-model")
        self.assertEqual(
            set(trace.stage_elapsed_ms),
            {"scope", "retrieval", "generation", "validation", "total"},
        )
        self.assertTrue(all(value >= 0 for value in trace.stage_elapsed_ms.values()))

    async def test_trace_recorder_failure_does_not_change_query_result(self):
        class FailingTraceRecorder:
            def record(self, trace):
                raise OSError("trace disk unavailable")

        evidence = EvidenceItem(
            evidence_id="ev_trace_failure",
            text="12小时降水量15.0~29.9毫米为大雨。",
            document_version_id="doc_rainfall",
            file_name="降水量等级.pdf",
            physical_pages=[4],
            heading_path=["表1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/rainfall.pdf",
        )
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([evidence]),
            answer_generator=InMemoryAnswerGenerator(
                Answer(
                    claims=[
                        Claim(
                            claim_id="claim-trace-failure",
                            text="12小时20毫米属于大雨。",
                            evidence_ids=[evidence.evidence_id],
                        )
                    ]
                )
            ),
            trace_recorder=FailingTraceRecorder(),
        )

        with self.assertLogs("src.application.query_service", level="ERROR"):
            result = await service.execute(
                QueryRequest(
                    request_id="query-trace-recorder-failure",
                    question="雨量等级怎么划分？",
                    conversation_context={},
                    caller_type="web",
                    corpus_version="corpus-v1",
                )
            )

        self.assertEqual(result.status, QueryStatus.ANSWERED)


if __name__ == "__main__":
    unittest.main()
