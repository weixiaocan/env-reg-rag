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
        limit: int = 10,
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
            allowed = (
                {fixed_usage_policy}
                if isinstance(fixed_usage_policy, str)
                else set(fixed_usage_policy)
            )
            hits = [hit for hit in hits if hit.usage_policy in allowed]
        hits = hits[: self._limit]
        return EvidencePack(
            corpus_version=corpus_version,
            evidence=[evidence_item_from_hit(hit) for hit in hits],
        )


_STRUCTURED_TYPES = {"table", "formula"}
_ELEMENT_ID = re.compile(r"^e(\d{6})$")
# A note after a table ("注：…", a unit line) is short; longer prose after a
# table belongs to the next topic.
_TRAILING_NOTE_MAX_CHARS = 120


def _element_number(element_id: str) -> int | None:
    match = _ELEMENT_ID.match(element_id)
    return int(match.group(1)) if match else None


class QdrantContextExpander:
    """Attach the prose framing each table/formula in the generation context.

    Tables and formulas are atomic chunks, so the sentence that introduces
    them ("降雨分为7个等级，具体划分见表1") and short notes such as units
    live in neighbouring chunks.  Canonical element ids are sequential in
    reading order, so the framing prose is the nearest text-only chunk before
    the structured element (within ``lookback`` elements) plus a short
    text-only chunk right after it.  Only chunks whose policy is allowed are
    added, at most ``max_extra`` in total.
    """

    def __init__(
        self,
        *,
        index: QdrantRetrievalIndex,
        allowed_policies: set[str],
        max_extra: int = 3,
        lookback: int = 3,
    ) -> None:
        self._index = index
        self._allowed = set(allowed_policies)
        self._max_extra = max_extra
        self._lookback = lookback

    async def expand(self, context: list[EvidenceItem]) -> list[EvidenceItem]:
        return await asyncio.to_thread(self._expand, context)

    def _expand(self, context: list[EvidenceItem]) -> list[EvidenceItem]:
        seen = {item.evidence_id for item in context}
        extras: list[EvidenceItem] = []
        for hit in self._index.find_by_chunk_ids([item.evidence_id for item in context]):
            if len(extras) >= self._max_extra:
                break
            if not _STRUCTURED_TYPES & set(hit.element_types):
                continue
            numbers = [n for n in map(_element_number, hit.element_ids) if n is not None]
            if not numbers:
                continue
            before = [f"e{n:06d}" for n in range(min(numbers) - 1, min(numbers) - 1 - self._lookback, -1) if n > 0]
            after = f"e{max(numbers) + 1:06d}"
            neighbours = self._index.find_by_elements(hit.document_version_id, [*before, after])
            # Framing prose shares the structured element's heading; prose
            # under another clause is a different topic.
            prose = [
                n for n in neighbours
                if set(n.element_types) <= {"text"} and n.heading_path == hit.heading_path
            ]
            preceding = sorted(
                (n for n in prose if set(n.element_ids) & set(before)),
                key=lambda n: -max(_element_number(e) or 0 for e in n.element_ids),
            )
            trailing = [
                n for n in prose
                if after in n.element_ids and len(n.text) <= _TRAILING_NOTE_MAX_CHARS
            ]
            # Only the nearest chunk on each side; if it is already in the
            # context nothing further away is pulled in.
            candidates = [
                n for n in [*preceding[:1], *trailing[:1]]
                if n.chunk_id not in seen and n.usage_policy in self._allowed
            ]
            for neighbour in candidates:
                if len(extras) >= self._max_extra or neighbour.chunk_id in seen:
                    continue
                seen.add(neighbour.chunk_id)
                extras.append(evidence_item_from_hit(neighbour))
        return extras
