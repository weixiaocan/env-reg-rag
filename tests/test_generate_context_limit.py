import unittest
from src.application.answer_generation import AnswerGenerationResult, ModelUsage
from src.application.query_observability import InMemoryQueryTraceRecorder
from src.application.query_service import QueryApplicationService
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import Answer, Claim, EvidenceItem, EvidencePack, QueryRequest, QueryStatus


class _RecordingGenerator:
    def __init__(self, answer: Answer):
        self.answer = answer
        self.last_evidence_ids = []
        self.last_count = 0

    async def generate(self, *, request, evidence_pack: EvidencePack):
        self.last_evidence_ids = [item.evidence_id for item in evidence_pack.evidence]
        self.last_count = len(evidence_pack.evidence)
        return AnswerGenerationResult(
            answer=self.answer,
            usage=ModelUsage(pricing_status="not_used"),
        )


def _item(i: int) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"ev_{i}",
        text=f"evidence text {i}",
        document_version_id="doc_a",
        file_name="a.pdf",
        physical_pages=[1],
        heading_path=["1"],
        usage_policy="answer_and_citation",
        source_uri="https://example.org/a.pdf",
    )


class GenerateContextLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_retrieve_ten_but_generate_with_top_five(self):
        evidence = [_item(i) for i in range(1, 11)]
        answer = Answer(
            claims=[
                Claim(
                    claim_id="claim-1",
                    text="supported by top evidence",
                    evidence_ids=["ev_1"],
                )
            ]
        )
        generator = _RecordingGenerator(answer)
        recorder = InMemoryQueryTraceRecorder()
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever(evidence),
            answer_generator=generator,
            generate_context_limit=5,
            trace_recorder=recorder,
        )
        result = await service.execute(
            QueryRequest(
                request_id="q-topk-split",
                question="城镇排水管道混接调查的基本要求是什么？",
                conversation_context={},
                corpus_version="formal-corpus-v1",
                caller_type="web",
            )
        )
        self.assertEqual(result.status, QueryStatus.ANSWERED)
        self.assertEqual(len(result.evidence), 10)
        self.assertEqual(generator.last_count, 5)
        self.assertEqual(generator.last_evidence_ids, [f"ev_{i}" for i in range(1, 6)])
        self.assertEqual(len(recorder.traces), 1)
        trace = recorder.traces[0]
        self.assertEqual(len(trace.evidence_ids), 10)
        self.assertEqual(
            trace.generate_context_evidence_ids,
            [f"ev_{i}" for i in range(1, 6)],
        )


if __name__ == "__main__":
    unittest.main()
