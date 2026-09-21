"""Answer-generation seam; provider-specific LangChain adapters sit behind it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.domain.query import Answer, EvidencePack, QueryRequest


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    pricing_status: str = "not_reported"


@dataclass(frozen=True)
class AnswerGenerationResult:
    answer: Answer
    usage: ModelUsage = ModelUsage()


class AnswerGenerator(Protocol):
    async def generate(
        self,
        *,
        request: QueryRequest,
        evidence_pack: EvidencePack,
    ) -> AnswerGenerationResult: ...


class InMemoryAnswerGenerator:
    """Deterministic adapter for query-module acceptance scenarios."""

    def __init__(self, answer: Answer | AnswerGenerationResult | None) -> None:
        self._answer = answer

    async def generate(
        self,
        *,
        request: QueryRequest,
        evidence_pack: EvidencePack,
    ) -> AnswerGenerationResult:
        if self._answer is None:
            raise RuntimeError("no in-memory answer was configured")
        if isinstance(self._answer, AnswerGenerationResult):
            return self._answer
        return AnswerGenerationResult(answer=self._answer)
