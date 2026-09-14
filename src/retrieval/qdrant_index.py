"""Qdrant-backed retrieval projection for evidence chunks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    text: str
    primary_evidence_id: str
    physical_pages: list[int]
    usage_policy: str
    score: float
    document_version_id: str = ""
    file_name: str = ""
    heading_path: list[str] = field(default_factory=list)
    source_uri: str = ""
    jurisdiction: str = "unknown"
    document_kind: str = "unknown"
    effective_status: str = "unknown"
    source_authority: str = ""
    publication_date: str = ""
    effective_from: str = ""
    effective_to: str = ""


@dataclass(frozen=True)
class HybridQuery:
    """Text and dense representations of the same user question."""

    text: str
    dense_vector: Sequence[float]


class QdrantRetrievalIndex:
    """Build and query the project's rebuildable Qdrant projection."""

    def __init__(
        self,
        *,
        client: QdrantClient,
        collection_name: str,
        vector_size: int,
        enable_bm25: bool = False,
        hybrid_prefetch_limit: int = 20,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._enable_bm25 = enable_bm25
        self._hybrid_prefetch_limit = hybrid_prefetch_limit

    def build(
        self,
        chunks: Sequence[dict[str, Any]],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("each retrieval chunk must have exactly one dense vector")
        if any(len(vector) != self._vector_size for vector in vectors):
            raise ValueError("dense vector size does not match the collection schema")

        if self._client.collection_exists(self._collection_name):
            self._client.delete_collection(self._collection_name)
        sparse_vectors_config = None
        if self._enable_bm25:
            sparse_vectors_config = {
                "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
            }
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=self._vector_size,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config=sparse_vectors_config,
        )
        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            named_vectors: dict[str, Any] = {"dense": list(vector)}
            if self._enable_bm25:
                named_vectors["bm25"] = models.Document(
                    text=chunk["text"],
                    model="qdrant/bm25",
                    options=self._bm25_options(),
                )
            points.append(
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, chunk["chunk_id"])),
                    vector=named_vectors,
                    payload={**chunk, **chunk.get("metadata", {})},
                )
            )
        if points:
            self._client.upsert(
                collection_name=self._collection_name,
                points=points,
                wait=True,
            )

    def search(
        self,
        query_vector: Sequence[float] | str | HybridQuery,
        *,
        mode: str,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[RetrievalHit]:
        query_filter = self._build_filter(filters)
        if mode == "exact_dense":
            if isinstance(query_vector, str):
                raise TypeError("exact_dense requires a numeric query vector")
            response = self._client.query_points(
                collection_name=self._collection_name,
                query=list(query_vector),
                using="dense",
                search_params=models.SearchParams(exact=True),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        elif mode == "ann_dense":
            if isinstance(query_vector, (str, HybridQuery)):
                raise TypeError("ann_dense requires a numeric query vector")
            response = self._client.query_points(
                collection_name=self._collection_name,
                query=list(query_vector),
                using="dense",
                search_params=models.SearchParams(exact=False),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        elif mode == "bm25":
            if not self._enable_bm25:
                raise ValueError("BM25 is not enabled for this index")
            if not isinstance(query_vector, str):
                raise TypeError("bm25 requires a text query")
            response = self._client.query_points(
                collection_name=self._collection_name,
                query=models.Document(
                    text=query_vector,
                    model="qdrant/bm25",
                    options=self._bm25_options(),
                ),
                using="bm25",
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        elif mode == "rrf":
            if not self._enable_bm25:
                raise ValueError("BM25 is not enabled for this index")
            if not isinstance(query_vector, HybridQuery):
                raise TypeError("rrf requires a HybridQuery")
            if len(query_vector.dense_vector) != self._vector_size:
                raise ValueError("dense query vector size does not match the collection schema")
            response = self._client.query_points(
                collection_name=self._collection_name,
                prefetch=[
                    models.Prefetch(
                        query=list(query_vector.dense_vector),
                        using="dense",
                        params=models.SearchParams(exact=True),
                        filter=query_filter,
                        limit=self._hybrid_prefetch_limit,
                    ),
                    models.Prefetch(
                        query=models.Document(
                            text=query_vector.text,
                            model="qdrant/bm25",
                            options=self._bm25_options(),
                        ),
                        using="bm25",
                        filter=query_filter,
                        limit=self._hybrid_prefetch_limit,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        else:
            raise ValueError(f"unsupported retrieval mode: {mode}")
        return [self._to_hit(point, score=float(point.score)) for point in response.points]

    def find_by_evidence_ids(self, evidence_ids: Sequence[str]) -> list[RetrievalHit]:
        """Fetch published chunks for deterministic matches, preserving ID order."""
        ordered_ids = list(dict.fromkeys(evidence_ids))
        if not ordered_ids:
            return []
        records, _ = self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="primary_evidence_id",
                        match=models.MatchAny(any=ordered_ids),
                    )
                ]
            ),
            limit=max(len(ordered_ids) * 4, 10),
            with_payload=True,
            with_vectors=False,
        )
        by_evidence_id: dict[str, RetrievalHit] = {}
        for record in records:
            hit = self._to_hit(record, score=1.0)
            by_evidence_id.setdefault(hit.primary_evidence_id, hit)
        return [by_evidence_id[item] for item in ordered_ids if item in by_evidence_id]

    @staticmethod
    def _to_hit(point: Any, *, score: float) -> RetrievalHit:
        payload = point.payload or {}
        return RetrievalHit(
            chunk_id=str(payload["chunk_id"]),
            text=str(payload["text"]),
            primary_evidence_id=str(payload["primary_evidence_id"]),
            physical_pages=list(payload["physical_pages"]),
            usage_policy=str(payload["usage_policy"]),
            score=score,
            document_version_id=str(payload.get("document_version_id", "")),
            file_name=str(payload.get("file_name", "")),
            heading_path=list(payload.get("heading_path", [])),
            source_uri=str(
                payload.get("source_uri") or payload.get("official_source_uri") or ""
            ),
            jurisdiction=str(payload.get("jurisdiction", "unknown")),
            document_kind=str(payload.get("document_kind", "unknown")),
            effective_status=str(payload.get("effective_status", "unknown")),
            source_authority=str(payload.get("source_authority", "")),
            publication_date=str(payload.get("publication_date", "")),
            effective_from=str(payload.get("effective_from", "")),
            effective_to=str(payload.get("effective_to", "")),
        )

    @staticmethod
    def _bm25_options() -> models.Bm25Config:
        return models.Bm25Config(
            tokenizer=models.TokenizerType.MULTILINGUAL,
            stemmer=models.DisabledStemmerParams(type=models.NoStemmer.NONE),
            stopwords=models.StopwordsSet(),
        )

    @staticmethod
    def _build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
        if not filters:
            return None

        must: list[models.Condition] = []
        for key, value in filters.items():
            if key == "as_of":
                try:
                    as_of = date.fromisoformat(str(value))
                except ValueError as exc:
                    raise ValueError("as_of must use ISO date format YYYY-MM-DD") from exc
                must.extend(
                    [
                        models.FieldCondition(
                            key="effective_from",
                            range=models.DatetimeRange(lte=as_of),
                        ),
                        models.FieldCondition(
                            key="effective_to",
                            range=models.DatetimeRange(gte=as_of),
                        ),
                    ]
                )
            else:
                if isinstance(value, (list, tuple, set)):
                    match = models.MatchAny(any=list(value))
                else:
                    match = models.MatchValue(value=value)
                must.append(
                    models.FieldCondition(
                        key=key,
                        match=match,
                    )
                )
        return models.Filter(must=must)
