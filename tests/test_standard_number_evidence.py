"""The standard number travels from the index payload to the answer model.

A file name need not carry the number (武汉市排水管道混错接改造技术规程.pdf),
so the model reads it from the registered document facts instead.
"""

import json
import unittest

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.adapters.langchain_answer_generator import LangChainAnswerGenerator
from src.adapters.qdrant_evidence_retriever import evidence_item_from_hit
from src.domain.query import EvidencePack, QueryRequest
from src.retrieval.qdrant_index import RetrievalHit


SEEN_MESSAGES: list = []


class _RecordingChatModel(FakeListChatModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        SEEN_MESSAGES.append(list(messages))
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


HIT = RetrievalHit(
    chunk_id="chunk_scope",
    text="1 范围\n本文件规定了武汉市排水管道混错接改造工作中的目标、原则和要求。",
    primary_evidence_id="chunk_scope",
    physical_pages=[5],
    usage_policy="answer_and_citation",
    score=1.0,
    file_name="武汉市排水管道混错接改造技术规程.pdf",
    standard_number="DB4201/T 651-2021",
)


class StandardNumberEvidenceTest(unittest.IsolatedAsyncioTestCase):
    def test_evidence_keeps_the_standard_number_of_its_hit(self):
        self.assertEqual(evidence_item_from_hit(HIT).standard_number, "DB4201/T 651-2021")

    async def test_answer_model_sees_the_standard_number(self):
        SEEN_MESSAGES.clear()
        model = _RecordingChatModel(responses=['{"answer":"标准号为DB4201/T 651-2021[S1]。"}'])
        request = QueryRequest(
            request_id="q", question="武汉规程的标准号是什么？", conversation_context={},
            corpus_version="corpus-test", caller_type="web",
        )

        await LangChainAnswerGenerator(model=model).generate(
            request=request,
            evidence_pack=EvidencePack(corpus_version="corpus-test",
                                       evidence=[evidence_item_from_hit(HIT)]),
        )

        question_message = SEEN_MESSAGES[0][1].content
        self.assertIn(json.dumps({"standard_number": "DB4201/T 651-2021"},
                                 ensure_ascii=False)[1:-1], question_message)


if __name__ == "__main__":
    unittest.main()
