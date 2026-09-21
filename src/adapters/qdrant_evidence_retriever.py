"""Production EvidenceRetriever backed by the published Qdrant collection."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.domain.query import EvidenceItem, EvidencePack
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex


_SUPPORTED_FILTERS = {
    "jurisdiction",
    "document_kind",
    "effective_status",
    "as_of",
    "usage_policy",
}
_SOURCE_CLAUSE_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+){2,})(?=\s|[\u4e00-\u9fff])"
)


def _evidence_locator_path(*, heading_path: list[str], text: str) -> list[str]:
    if heading_path:
        return list(heading_path)
    match = _SOURCE_CLAUSE_PATTERN.search(text)
    return [match.group(1)] if match else []


def evidence_item_from_hit(hit: Any) -> EvidenceItem:
    """Map one retrieval projection record into the public evidence model."""
    return EvidenceItem(
        evidence_id=hit.chunk_id,
        text=hit.text,
        document_version_id=hit.document_version_id,
        file_name=hit.file_name,
        physical_pages=hit.physical_pages,
        heading_path=_evidence_locator_path(
            heading_path=hit.heading_path,
            text=hit.text,
        ),
        usage_policy=hit.usage_policy,
        source_uri=hit.source_uri,
        jurisdiction=hit.jurisdiction,
        document_kind=hit.document_kind,
        effective_status=hit.effective_status,
        source_authority=hit.source_authority,
        publication_date=hit.publication_date,
        effective_from=hit.effective_from,
        effective_to=hit.effective_to,
        source_regions=getattr(hit, 'source_regions', []),
    )


class QdrantEvidenceRetriever:
    """Hide embedding, RRF querying, filters, and evidence mapping behind one seam."""

    def __init__(
        self,
        *,
        index: QdrantRetrievalIndex,
        embedder: Any,
        fixed_filters: dict[str, Any] | None = None,
        limit: int = 5,
    ) -> None:
        self._index = index
        self._embedder = embedder
        self._fixed_filters = dict(fixed_filters or {})
        self._limit = limit

    async def retrieve(
        self,
        *,
        question: str,
        resolved_scope: dict[str, str],
        corpus_version: str,
    ) -> EvidencePack:
        dense_vector = await asyncio.to_thread(self._embedder.embed_query, question)
        requested_filters = {
            key: value
            for key, value in resolved_scope.items()
            if key in _SUPPORTED_FILTERS
        }
        filters = {**requested_filters, **self._fixed_filters}
        requested_jurisdiction = filters.get("jurisdiction")
        if requested_jurisdiction and requested_jurisdiction != "全国":
            filters["jurisdiction"] = [requested_jurisdiction, "全国"]
        hits = await asyncio.to_thread(
            self._index.search,
            HybridQuery(text=question, dense_vector=dense_vector),
            mode="rrf",
            filters=filters or None,
            limit=self._limit * 2 if requested_jurisdiction else self._limit,
        )
        if requested_jurisdiction and requested_jurisdiction != "全国":
            hits = [
                *[hit for hit in hits if hit.jurisdiction == requested_jurisdiction],
                *[hit for hit in hits if hit.jurisdiction == "全国"],
            ]
        fixed_usage_policy = self._fixed_filters.get("usage_policy")
        if fixed_usage_policy:
            hits = [
                hit for hit in hits if hit.usage_policy == fixed_usage_policy
            ]
        hits = hits[: self._limit]
        return EvidencePack(
            corpus_version=corpus_version,
            evidence=[evidence_item_from_hit(hit) for hit in hits],
        )
