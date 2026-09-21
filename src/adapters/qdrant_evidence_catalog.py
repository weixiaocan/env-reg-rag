"""Deterministic evidence lookup across the published Qdrant collection."""

from __future__ import annotations

from src.adapters.qdrant_evidence_retriever import evidence_item_from_hit
from src.domain.query import EvidenceItem
from src.retrieval.qdrant_index import QdrantRetrievalIndex


class QdrantEvidenceCatalog:
    """Fetch one stable evidence ID (chunk_id) from the V2 collection."""

    def __init__(self, *, index: QdrantRetrievalIndex) -> None:
        self._index = index

    def get(self, evidence_id: str) -> EvidenceItem | None:
        hits = self._index.find_by_evidence_ids([evidence_id])
        if hits:
            return evidence_item_from_hit(hits[0])
        return None
