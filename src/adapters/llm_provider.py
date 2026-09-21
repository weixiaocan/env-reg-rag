"""Configuration and composition for external answer-model providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.adapters.langchain_answer_generator import LangChainAnswerGenerator


class LlmConfigurationError(ValueError):
    """The selected model provider cannot be configured safely."""


@dataclass(frozen=True)
class _ProviderProfile:
    key_variable: str
    base_url: str | None
    default_model: str | None


_PROVIDERS = {
    "zhipu": _ProviderProfile(
        key_variable="ZAI_API_KEY",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        default_model="glm-5.2",
    ),
    "openai": _ProviderProfile(
        key_variable="OPENAI_API_KEY",
        base_url=None,
        default_model=None,
    ),
    "deepseek": _ProviderProfile(
        key_variable="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        default_model=None,
    ),
}


@dataclass(frozen=True)
class LlmProviderSettings:
    provider: str
    model: str
    api_key: SecretStr
    base_url: str | None
    temperature: float = 0.1
    timeout_seconds: float = 60.0
    max_retries: int = 2
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cost_currency: str = "CNY"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "LlmProviderSettings":
        values = os.environ if env is None else env
        provider = values.get("LLM_PROVIDER", "zhipu").strip().lower()
        profile = _PROVIDERS.get(provider)
        if profile is None:
            supported = ", ".join(sorted(_PROVIDERS))
            raise LlmConfigurationError(
                f"unsupported LLM_PROVIDER {provider!r}; choose one of: {supported}"
            )
        raw_key = values.get(profile.key_variable, "").strip()
        if not raw_key:
            raise LlmConfigurationError(
                f"{profile.key_variable} is required for provider {provider}"
            )
        model = values.get("LLM_MODEL", "").strip() or profile.default_model
        if not model:
            raise LlmConfigurationError(
                f"LLM_MODEL is required for provider {provider}"
            )
        base_url = values.get("LLM_BASE_URL", "").strip() or profile.base_url
        raw_input_cost = values.get("LLM_INPUT_COST_PER_MILLION", "").strip()
        raw_output_cost = values.get("LLM_OUTPUT_COST_PER_MILLION", "").strip()
        if bool(raw_input_cost) != bool(raw_output_cost):
            raise LlmConfigurationError(
                "both LLM_INPUT_COST_PER_MILLION and LLM_OUTPUT_COST_PER_MILLION are required for cost estimation"
            )
        return cls(
            provider=provider,
            model=model,
            api_key=SecretStr(raw_key),
            base_url=base_url,
            temperature=float(values.get("LLM_TEMPERATURE", "0.1")),
            timeout_seconds=float(values.get("LLM_TIMEOUT_SECONDS", "60")),
            max_retries=int(values.get("LLM_MAX_RETRIES", "2")),
            input_cost_per_million=(float(raw_input_cost) if raw_input_cost else None),
            output_cost_per_million=(float(raw_output_cost) if raw_output_cost else None),
            cost_currency=values.get("LLM_COST_CURRENCY", "CNY").strip() or "CNY",
        )


def build_answer_generator(
    settings: LlmProviderSettings,
) -> LangChainAnswerGenerator:
    """Compose the provider-neutral answer adapter used by the query module."""

    model = ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.temperature,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    return LangChainAnswerGenerator(
        model=model,
        input_cost_per_million=settings.input_cost_per_million,
        output_cost_per_million=settings.output_cost_per_million,
        cost_currency=settings.cost_currency,
    )
