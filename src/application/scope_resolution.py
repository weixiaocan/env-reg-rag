"""Resolve applicability conditions before retrieval or answer generation."""

from __future__ import annotations

from typing import Protocol

from src.domain.query import QueryRequest, ScopeResolution


class ScopeResolver(Protocol):
    async def resolve(self, request: QueryRequest) -> ScopeResolution: ...


class PassthroughScopeResolver:
    """Pass the question through unchanged.

    Conditions the question leaves open (statistical period, region, object)
    are no longer guessed or asked for by hand-written rules; the answer
    generator states the alternatives when the conclusion depends on them
    (owner decision 2026-10-19).
    """

    async def resolve(self, request: QueryRequest) -> ScopeResolution:
        resolved = dict(request.conversation_context)
        resolved.pop("pending_question", None)
        resolved.pop("anchor_question", None)
        return ScopeResolution(
            resolved_scope=resolved,
            missing_conditions=[],
            effective_question=request.question,
            anchor_question=request.question,
        )
