import unittest

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from src.adapters.langchain_answer_generator import LangChainAnswerGenerator
from src.domain.query import EvidencePack, QueryRequest


class LangChainModelUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_usage_is_recorded_with_configured_token_pricing(self):
        model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content='{"claims":[]}',
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "total_tokens": 120,
                    },
                )
            ]
        )
        generator = LangChainAnswerGenerator(
            model=model,
            input_cost_per_million=1.0,
            output_cost_per_million=2.0,
            cost_currency="CNY",
        )

        generated = await generator.generate(
            request=QueryRequest(
                request_id="query-usage-001",
                question="测试",
                conversation_context={},
                corpus_version="formal-corpus-v1",
                caller_type="test",
            ),
            evidence_pack=EvidencePack(
                corpus_version="formal-corpus-v1",
                evidence=[],
            ),
        )

        self.assertEqual(generated.usage.input_tokens, 100)
        self.assertEqual(generated.usage.output_tokens, 20)
        self.assertEqual(generated.usage.total_tokens, 120)
        self.assertAlmostEqual(generated.usage.estimated_cost, 0.00014)
        self.assertEqual(generated.usage.currency, "CNY")
        self.assertEqual(generated.usage.pricing_status, "calculated")


if __name__ == "__main__":
    unittest.main()
