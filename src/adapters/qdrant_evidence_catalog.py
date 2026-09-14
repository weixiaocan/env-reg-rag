"""Deterministic evidence lookup across released Qdrant projections."""

from __future__ import annotations

from src.adapters.qdrant_evidence_retriever import evidence_item_from_hit
from src.domain.query import EvidenceItem
from src.retrieval.qdrant_index import QdrantRetrievalIndex


class QdrantEvidenceCatalog:
    """Fetch one stable evidence ID while preserving corpus safety policies."""

    def __init__(
        self,
        *,
        formal_index: QdrantRetrievalIndex,
        source_locator_index: QdrantRetrievalIndex,
    ) -> None:
        self._formal_index = formal_index
        self._source_locator_index = source_locator_index

    def get(self, evidence_id: str) -> EvidenceItem | None:
        formal_hits = self._formal_index.find_by_evidence_ids([evidence_id])
        if formal_hits:
            return evidence_item_from_hit(formal_hits[0])
        locator_hits = self._source_locator_index.find_by_evidence_ids([evidence_id])
        if locator_hits and locator_hits[0].usage_policy == "source_locator_only":
            return evidence_item_from_hit(locator_hits[0])
        return None
