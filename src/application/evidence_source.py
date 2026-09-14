"""Application boundary for reading one published evidence item."""

from __future__ import annotations

from typing import Protocol

from src.domain.query import EvidenceItem


class EvidenceRepository(Protocol):
    def get(self, evidence_id: str) -> EvidenceItem | None: ...


class EvidenceSourceService:
    """Expose only published, policy-labelled evidence through one stable port."""

    def __init__(self, repository: EvidenceRepository) -> None:
        self._repository = repository

    def get(self, evidence_id: str) -> EvidenceItem | None:
        if not evidence_id.strip():
            return None
        evidence = self._repository.get(evidence_id)
        if evidence is None or evidence.evidence_id != evidence_id:
            return None
        if evidence.usage_policy not in {
            "answer_and_citation",
            "source_locator_only",
        }:
            return None
        return evidence
