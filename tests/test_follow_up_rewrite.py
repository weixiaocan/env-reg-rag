"""Follow-ups are rewritten from the history before retrieval."""

import unittest

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.adapters.langchain_answer_generator import LangChainAnswerGenerator
from src.adapters.langchain_question_rewriter import LangChainQuestionRewriter
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.question_rewriting import RewriteResult
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import PassthroughScopeResolver
from src.domain.query import (
    Answer,
    Claim,
    ConversationTurn,
    EvidenceItem,
    EvidencePack,
    QueryRequest,
    QueryStatus,
)
from src.server.fastapi_app import create_app


EVIDENCE = EvidenceItem(
    evidence_id="ev_night_flow",
    text="5.2.5 采用夜间最小流量法计算旱天入流入渗量时，应按下式计算：BI = ADF - (ADF - MDF)/X",
    document_version_id="doc_tcecs1764",
    file_name="城镇污水管网入流入渗监测与评估标准TCECS1764-2024.pdf",
    physical_pages=[19],
    heading_path=["5 评估", "5.2 旱天入流入渗计算", "5.2.5"],
    usage_policy="answer_and_citation",
    source_uri="https://example.org/tcecs1764.pdf",
)
HISTORY = (
    ConversationTurn(
        question="夜间最小流量法是什么意思？",
        answer="夜间最小流量法是用污水管网夜间流量数据估算旱天入流入渗量的方法。",
    ),
)


class _RecordingRetriever:
    def __init__(self):
        self.questions = []

    async def retrieve(self, *, question, resolved_scope, corpus_version):
        self.questions.append(question)
        return EvidencePack(corpus_version=corpus_version, evidence=[EVIDENCE])


class _FixedRewriter:
    def __init__(self, question=None, error=None):
        self.calls = []
        self._question = question
        self._error = error

    async def rewrite(self, *, question, history):
        self.calls.append((question, tuple(history)))
        if self._error:
            raise self._error
        return RewriteResult(question=self._question)


SEEN_MESSAGES: list = []


class _RecordingChatModel(FakeListChatModel):
    """Fake chat model that keeps the messages of every call (module list:
    attributes on a pydantic model would become per-instance fields)."""

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN_MESSAGES.append(list(messages))
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _request(history=HISTORY, question="计算公式在哪里？"):
    return QueryRequest(
        request_id="q-follow-up",
        question=question,
        conversation_context={},
        corpus_version="corpus-test",
        caller_type="web",
        history=history,
    )


def _answer():
    return InMemoryAnswerGenerator(
        Answer(claims=[Claim(claim_id="claim-1", text="见 5.2.5 条。", evidence_ids=["ev_night_flow"])])
    )


class FollowUpRewriteServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_follow_up_is_searched_as_the_rewritten_question(self):
        retriever = _RecordingRetriever()
        rewriter = _FixedRewriter("夜间最小流量法的计算公式在哪里？")
        service = QueryApplicationService(
            scope_resolver=PassthroughScopeResolver(),
            evidence_retriever=retriever,
            answer_generator=_answer(),
            question_rewriter=rewriter,
        )

        result = await service.execute(_request())

        self.assertEqual(rewriter.calls, [("计算公式在哪里？", HISTORY)])
        self.assertEqual(retriever.questions, ["夜间最小流量法的计算公式在哪里？"])
        self.assertEqual(result.search_question, "夜间最小流量法的计算公式在哪里？")
        self.assertEqual(result.status, QueryStatus.ANSWERED)

    async def test_first_question_is_searched_as_asked(self):
        retriever = _RecordingRetriever()
        rewriter = _FixedRewriter("不应被调用")
        service = QueryApplicationService(
            scope_resolver=PassthroughScopeResolver(),
            evidence_retriever=retriever,
            answer_generator=_answer(),
            question_rewriter=rewriter,
        )

        result = await service.execute(_request(history=(), question="夜间最小流量法是什么意思？"))

        self.assertEqual(rewriter.calls, [])
        self.assertEqual(retriever.questions, ["夜间最小流量法是什么意思？"])
        self.assertEqual(result.search_question, "")

    async def test_failed_rewrite_falls_back_to_the_question_as_asked(self):
        retriever = _RecordingRetriever()
        service = QueryApplicationService(
            scope_resolver=PassthroughScopeResolver(),
            evidence_retriever=retriever,
            answer_generator=_answer(),
            question_rewriter=_FixedRewriter(error=TimeoutError("provider timeout")),
        )

        result = await service.execute(_request())

        self.assertEqual(retriever.questions, ["计算公式在哪里？"])
        self.assertEqual(result.status, QueryStatus.ANSWERED)


class FollowUpPromptTest(unittest.IsolatedAsyncioTestCase):
    async def test_answer_model_sees_history_in_the_question_message(self):
        SEEN_MESSAGES.clear()
        model = _RecordingChatModel(responses=['{"answer":"公式见第5.2.5条[S1]。"}'])
        generator = LangChainAnswerGenerator(model=model)
        request = QueryRequest(
            request_id="q", question="计算公式在哪里？", conversation_context={},
            corpus_version="corpus-test", caller_type="web", history=HISTORY,
            search_question="夜间最小流量法的计算公式在哪里？",
        )

        await generator.generate(
            request=request,
            evidence_pack=EvidencePack(corpus_version="corpus-test", evidence=[EVIDENCE]),
        )

        messages = SEEN_MESSAGES[0]
        # Earlier answers are never replayed as assistant turns: plain-text
        # replies would teach the model to drop the JSON answer format.
        self.assertEqual([type(message) for message in messages], [SystemMessage, HumanMessage])
        question_message = messages[1].content
        self.assertIn("用户：夜间最小流量法是什么意思？", question_message)
        self.assertIn("助手：夜间最小流量法是用污水管网", question_message)
        self.assertIn("用户问题：计算公式在哪里？", question_message)
        self.assertIn("夜间最小流量法的计算公式在哪里？", question_message)
        self.assertIn("不用 LaTeX", messages[0].content)

    async def test_rewriter_returns_one_clean_question(self):
        rewriter = LangChainQuestionRewriter(
            model=FakeListChatModel(responses=["改写后的问题：“夜间最小流量法的计算公式在哪里？”"])
        )

        result = await rewriter.rewrite(question="计算公式在哪里？", history=HISTORY)

        self.assertEqual(result.question, "夜间最小流量法的计算公式在哪里？")

    async def test_rewriter_keeps_the_question_when_the_model_returns_nothing(self):
        rewriter = LangChainQuestionRewriter(model=FakeListChatModel(responses=["   "]))

        result = await rewriter.rewrite(question="计算公式在哪里？", history=HISTORY)

        self.assertEqual(result.question, "计算公式在哪里？")


class FollowUpHttpTest(unittest.TestCase):
    def test_history_reaches_the_service_capped_at_three_turns(self):
        retriever = _RecordingRetriever()
        rewriter = _FixedRewriter("夜间最小流量法的计算公式在哪里？")
        service = QueryApplicationService(
            scope_resolver=PassthroughScopeResolver(),
            evidence_retriever=retriever,
            answer_generator=_answer(),
            question_rewriter=rewriter,
        )
        client = TestClient(create_app(query_service=service))
        history = [{"question": f"问题{i}", "answer": f"回答{i}"} for i in range(5)]

        response = client.post(
            "/api/v1/queries",
            json={"question": "计算公式在哪里？", "history": history},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["search_question"], "夜间最小流量法的计算公式在哪里？")
        sent = rewriter.calls[0][1]
        self.assertEqual([turn.question for turn in sent], ["问题2", "问题3", "问题4"])


if __name__ == "__main__":
    unittest.main()
