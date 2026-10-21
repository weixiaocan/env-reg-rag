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
# What follows a table or formula inside the same clause explains it: the
# "式中" variable lines, units, "注：…" notes.  Each is short; longer prose is
# the clause moving on.  The walk stops at the next clause.
_TRAILING_NOTE_MAX_CHARS = 160
# A run of "式中" variable lines ("Rj——第j场降雨的降雨量监测值（mm）；") can
# be longer than a note and still only explain the formula.
_VARIABLE_LIST_MAX_CHARS = 600
_TRAILING_SPAN = 10
# Prose that points forward at a table or formula ("见表1", "按下式计算").
_STRUCTURE_REFERENCE = re.compile(r"见\s*[表式]|下[表式]|如下公式|按式")


def _element_number(element_id: str) -> int | None:
    match = _ELEMENT_ID.match(element_id)
    return int(match.group(1)) if match else None


class QdrantContextExpander:
    """Attach the prose framing each table/formula in the generation context.

    Tables and formulas are atomic chunks, so the sentence that introduces
    them ("降雨分为7个等级，具体划分见表1") and short notes such as units
    live in neighbouring chunks.  Canonical element ids are sequential in
    reading order, so the framing prose is the nearest text-only chunk before
    the structured element (within ``lookback`` elements) plus the short
    chunks after it in the same clause (variable lines, units, notes).  Conversely, prose that points at a
    table or formula ("见表1") brings in the structured chunk it introduces.
    Only chunks whose policy is allowed are
    added, at most ``max_extra`` in total.
    """

    def __init__(
        self,
        *,
        index: QdrantRetrievalIndex,
        allowed_policies: set[str],
        max_extra: int = 8,
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
                table = self._referenced_structure(hit)
                if (
                    table is not None
                    and table.chunk_id not in seen
                    and table.usage_policy in self._allowed
                ):
                    seen.add(table.chunk_id)
                    extras.append(evidence_item_from_hit(table))
                continue
            numbers = [n for n in map(_element_number, hit.element_ids) if n is not None]
            if not numbers:
                continue
            before = [f"e{n:06d}" for n in range(min(numbers) - 1, min(numbers) - 1 - self._lookback, -1) if n > 0]
            after = [f"e{n:06d}" for n in range(max(numbers) + 1, max(numbers) + 1 + _TRAILING_SPAN)]
            neighbours = self._index.find_by_elements(hit.document_version_id, [*before, *after])
            # Framing prose shares the structured element's heading; prose
            # under another clause is a different topic.
            preceding = sorted(
                (
                    n for n in neighbours
                    if set(n.element_ids) & set(before)
                    and set(n.element_types) <= {"text"}
                    and n.heading_path == hit.heading_path
                ),
                key=lambda n: -max(_element_number(e) or 0 for e in n.element_ids),
            )
            # Only the nearest lead-in; if it is already in the context
            # nothing further away is pulled in.
            candidates = [
                n for n in [*preceding[:1], *self._explanation(hit, neighbours, after)]
                if n.chunk_id not in seen and n.usage_policy in self._allowed
            ]
            for neighbour in candidates:
                if len(extras) >= self._max_extra or neighbour.chunk_id in seen:
                    continue
                seen.add(neighbour.chunk_id)
                extras.append(evidence_item_from_hit(neighbour))
        return extras

    @staticmethod
    def _explanation(hit: Any, neighbours: list[Any], after: list[str]) -> list[Any]:
        """Chunks right after a table/formula that explain it, in order.

        Formula chunks without "=" are unit fragments the layout split off
        ("(m³/d)") and are skipped; any other structured chunk, a long
        paragraph, or a new heading ends the clause.
        """
        following = sorted(
            (n for n in neighbours if set(n.element_ids) & set(after)),
            key=lambda n: min(_element_number(e) or 0 for e in n.element_ids),
        )
        explanation = []
        for n in following:
            if n.heading_path != hit.heading_path:
                break
            body = n.text.rsplit("[公式]", 1)[-1]
            if "formula" in n.element_types and "=" not in body:
                continue
            if not set(n.element_types) <= {"text"}:
                break
            # Chunk text starts with its heading line; only the body counts.
            heading = " > ".join(n.heading_path)
            prose = n.text[len(heading):].strip() if heading and n.text.startswith(heading) else n.text
            limit = _VARIABLE_LIST_MAX_CHARS if "——" in prose else _TRAILING_NOTE_MAX_CHARS
            if len(prose) > limit:
                break
            explanation.append(n)
        return explanation

    def _referenced_structure(self, hit: Any) -> Any | None:
        """Return the table/formula a lead-in sentence points at, if any.

        The reverse of the lead-in lookup: prose such as "具体划分见表1" can
        rank inside the generation context while the table itself is cut
        by the context limit. Only the nearest structured chunk following
        the prose under the same heading is taken.
        """
        if not _STRUCTURE_REFERENCE.search(hit.text):
            return None
        numbers = [n for n in map(_element_number, hit.element_ids) if n is not None]
        if not numbers:
            return None
        following = [f"e{n:06d}" for n in range(max(numbers) + 1, max(numbers) + 1 + self._lookback)]
        neighbours = [
            n for n in self._index.find_by_elements(hit.document_version_id, following)
            if _STRUCTURED_TYPES & set(n.element_types) and n.heading_path == hit.heading_path
        ]
        if not neighbours:
            return None
        return min(
            neighbours,
            key=lambda n: min(_element_number(e) or 0 for e in n.element_ids if e in following),
        )
