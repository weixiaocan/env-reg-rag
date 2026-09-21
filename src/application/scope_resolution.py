"""Resolve applicability conditions before retrieval or answer generation."""

from __future__ import annotations

from typing import Protocol

from src.domain.query import QueryRequest, ScopeResolution


class ScopeResolver(Protocol):
    async def resolve(self, request: QueryRequest) -> ScopeResolution: ...


class RuleBasedScopeResolver:
    """Deterministic resolver for conditions that must never be silently guessed."""

    async def resolve(self, request: QueryRequest) -> ScopeResolution:
        resolved = dict(request.conversation_context)
        pending_question = resolved.pop("pending_question", "")
        inherited_anchor = resolved.pop("anchor_question", "")
        current_period = self._period_from_text(request.question)
        explicit_period = current_period or resolved.get("statistical_period")
        if explicit_period:
            resolved["statistical_period"] = explicit_period

        if (pending_question or inherited_anchor) and current_period and self._is_period_only(
            request.question
        ):
            anchor_question = pending_question or inherited_anchor
            effective_question = (
                f"{anchor_question}（统计时段：{current_period}）"
            )
        else:
            anchor_question = request.question
            effective_question = request.question

        normalized_question = effective_question.lower().replace(" ", "")
        has_rainfall_measure = "毫米" in normalized_question and (
            "雨量" in normalized_question
            or "降水量" in normalized_question
            or "降雨量" in normalized_question
        )

        missing = []
        if has_rainfall_measure and "statistical_period" not in resolved:
            missing.append("statistical_period")
        return ScopeResolution(
            resolved_scope=resolved,
            missing_conditions=missing,
            effective_question=effective_question,
            anchor_question=anchor_question,
        )

    @staticmethod
    def _period_from_text(text: str) -> str:
        normalized = text.lower().replace(" ", "")
        if "12小时" in normalized or "12h" in normalized:
            return "12h"
        if (
            "24小时" in normalized
            or "24h" in normalized
            or "日降雨量" in normalized
        ):
            return "24h"
        return ""

    @staticmethod
    def _is_period_only(text: str) -> bool:
        normalized = text.lower().replace(" ", "").strip("，。！？,.!?")
        return normalized in {"12小时", "12h", "24小时", "24h"}
