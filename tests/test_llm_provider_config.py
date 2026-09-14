import unittest

from src.adapters.llm_provider import LlmConfigurationError, LlmProviderSettings


class LlmProviderSettingsTest(unittest.TestCase):
    def test_zhipu_is_selected_without_reusing_the_openai_key(self):
        settings = LlmProviderSettings.from_env(
            {
                "LLM_PROVIDER": "zhipu",
                "LLM_MODEL": "glm-5.2",
                "ZAI_API_KEY": "zhipu-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
        )

        self.assertEqual(settings.provider, "zhipu")
        self.assertEqual(settings.model, "glm-5.2")
        self.assertEqual(settings.base_url, "https://open.bigmodel.cn/api/paas/v4/")
        self.assertEqual(settings.api_key.get_secret_value(), "zhipu-secret")

    def test_missing_provider_key_fails_without_exposing_other_keys(self):
        with self.assertRaisesRegex(LlmConfigurationError, "ZAI_API_KEY") as raised:
            LlmProviderSettings.from_env(
                {
                    "LLM_PROVIDER": "zhipu",
                    "OPENAI_API_KEY": "must-not-leak",
                }
            )

        self.assertNotIn("must-not-leak", str(raised.exception))

    def test_optional_token_prices_are_parsed_as_a_pair(self):
        settings = LlmProviderSettings.from_env(
            {
                "LLM_PROVIDER": "zhipu",
                "ZAI_API_KEY": "zhipu-secret",
                "LLM_INPUT_COST_PER_MILLION": "1.25",
                "LLM_OUTPUT_COST_PER_MILLION": "5.00",
                "LLM_COST_CURRENCY": "CNY",
            }
        )

        self.assertEqual(settings.input_cost_per_million, 1.25)
        self.assertEqual(settings.output_cost_per_million, 5.0)
        self.assertEqual(settings.cost_currency, "CNY")

    def test_partial_token_price_configuration_is_rejected(self):
        with self.assertRaisesRegex(LlmConfigurationError, "both LLM_INPUT"):
            LlmProviderSettings.from_env(
                {
                    "LLM_PROVIDER": "zhipu",
                    "ZAI_API_KEY": "zhipu-secret",
                    "LLM_INPUT_COST_PER_MILLION": "1.25",
                }
            )


if __name__ == "__main__":
    unittest.main()
