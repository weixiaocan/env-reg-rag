"""Evidence retrieval seam used by the query application module."""

from __future__ import annotations

from typing import Protocol

from src.domain.query import EvidenceItem, EvidencePack


class EvidenceRetriever(Protocol):
    async def retrieve(
        self,
        *,
        question: str,
        resolved_scope: dict[str, str],
        corpus_version: str,
    ) -> EvidencePack: ...


class InMemoryEvidenceRetriever:
    """Deterministic adapter for query-module acceptance scenarios."""

    def __init__(self, evidence: list[EvidenceItem]) -> None:
        self._evidence = list(evidence)

    async def retrieve(
        self,
        *,
        question: str,
        resolved_scope: dict[str, str],
        corpus_version: str,
    ) -> EvidencePack:
        return EvidencePack(
            corpus_version=corpus_version,
            evidence=list(self._evidence),
        )
