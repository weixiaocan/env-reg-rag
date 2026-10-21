"""Port for turning a follow-up question into a standalone search question."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from src.application.answer_generation import ModelUsage
from src.domain.query import ConversationTurn


@dataclass(frozen=True)
class RewriteResult:
    question: str
    usage: ModelUsage = field(default_factory=lambda: ModelUsage(pricing_status="not_used"))


class QuestionRewriter(Protocol):
    async def rewrite(
        self, *, question: str, history: Sequence[ConversationTurn]
    ) -> RewriteResult:
        """Return a question that can be searched without the conversation."""


def add_usage(first: ModelUsage, second: ModelUsage | None) -> ModelUsage:
    """Sum two model calls of one query; unknown counts stay unknown."""
    if second is None or second.pricing_status == "not_used":
        return first
    if first.pricing_status == "not_used":
        return second

    def total(a, b):
        return None if a is None or b is None else a + b

    return ModelUsage(
        input_tokens=total(first.input_tokens, second.input_tokens),
        output_tokens=total(first.output_tokens, second.output_tokens),
        total_tokens=total(first.total_tokens, second.total_tokens),
        estimated_cost=(
            None
            if first.estimated_cost is None or second.estimated_cost is None
            else round(first.estimated_cost + second.estimated_cost, 12)
        ),
        currency=first.currency or second.currency,
        pricing_status=(
            first.pricing_status
            if first.pricing_status == second.pricing_status
            else "partial"
        ),
    )
