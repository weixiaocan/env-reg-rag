"""Evaluate exact dense retrieval against approved Golden Set cases."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.retrieval.qdrant_index import HybridQuery


def evaluate_dense_retrieval(
    chunks: Sequence[dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    embedder: Any,
    index: Any,
    *,
    mode: str = "exact_dense",
    limit: int = 5,
) -> dict[str, Any]:
    if mode not in {"exact_dense", "ann_dense"}:
        raise ValueError(f"unsupported dense evaluation mode: {mode}")
    document_vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    index.build(chunks, document_vectors)

    case_results = []
    reciprocal_ranks = []
    recalls = {1: [], 3: [], 5: []}
    corpus_gap_results = []

    for case in cases:
        query_vector = embedder.embed_query(case["question"])
        hits = index.search(
            query_vector,
            mode=mode,
            filters=case.get("required_filters") or None,
            limit=limit,
        )
        rankings = [
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "primary_evidence_id": hit.primary_evidence_id,
                "physical_pages": hit.physical_pages,
                "usage_policy": hit.usage_policy,
                "score": hit.score,
            }
            for rank, hit in enumerate(hits, start=1)
        ]
        relevant_ids = set(case.get("required_evidence_ids", [])) | set(
            case.get("acceptable_evidence_ids", [])
        )
        first_relevant_rank = next(
            (
                item["rank"]
                for item in rankings
                if item["primary_evidence_id"] in relevant_ids
            ),
            None,
        )
        if relevant_ids:
            reciprocal_ranks.append(
                0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
            )
            for cutoff in recalls:
                recalls[cutoff].append(
                    float(
                        first_relevant_rank is not None
                        and first_relevant_rank <= cutoff
                    )
                )
        elif case.get("expected_outcome", "").startswith("no_answer"):
            corpus_gap_results.append(not rankings)

        case_results.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "expected_outcome": case["expected_outcome"],
                "required_filters": case.get("required_filters", {}),
                "relevant_evidence_ids": sorted(relevant_ids),
                "first_relevant_rank": first_relevant_rank,
                "rankings": rankings,
            }
        )

    positive_count = len(reciprocal_ranks)
    summary = {
        "case_count": len(cases),
        "positive_case_count": positive_count,
        "corpus_gap_case_count": len(corpus_gap_results),
        "recall_at_1": _mean(recalls[1]),
        "recall_at_3": _mean(recalls[3]),
        "recall_at_5": _mean(recalls[5]),
        "mrr": _mean(reciprocal_ranks),
        "corpus_gap_empty_rate": _mean(corpus_gap_results),
    }
    return {
        "retrieval_mode": mode,
        "model": {
            "model_id": embedder.model_id,
            "revision": embedder.model_revision,
            "dimension": embedder.dimension,
            "query_instruction": embedder.query_instruction,
        },
        "summary": summary,
        "cases": case_results,
    }


def evaluate_bm25_retrieval(
    chunks: Sequence[dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    embedder: Any,
    index: Any,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Evaluate server-side multilingual BM25 using the same metric contract."""

    document_vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    index.build(chunks, document_vectors)

    case_results = []
    reciprocal_ranks = []
    recalls = {1: [], 3: [], 5: []}
    corpus_gap_results = []

    for case in cases:
        hits = index.search(
            case["question"],
            mode="bm25",
            filters=case.get("required_filters") or None,
            limit=limit,
        )
        rankings = [
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "primary_evidence_id": hit.primary_evidence_id,
                "physical_pages": hit.physical_pages,
                "usage_policy": hit.usage_policy,
                "score": hit.score,
            }
            for rank, hit in enumerate(hits, start=1)
        ]
        relevant_ids = set(case.get("required_evidence_ids", [])) | set(
            case.get("acceptable_evidence_ids", [])
        )
        first_relevant_rank = next(
            (
                item["rank"]
                for item in rankings
                if item["primary_evidence_id"] in relevant_ids
            ),
            None,
        )
        if relevant_ids:
            reciprocal_ranks.append(
                0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
            )
            for cutoff in recalls:
                recalls[cutoff].append(
                    float(
                        first_relevant_rank is not None
                        and first_relevant_rank <= cutoff
                    )
                )
        elif case.get("expected_outcome", "").startswith("no_answer"):
            corpus_gap_results.append(not rankings)

        case_results.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "expected_outcome": case["expected_outcome"],
                "required_filters": case.get("required_filters", {}),
                "relevant_evidence_ids": sorted(relevant_ids),
                "first_relevant_rank": first_relevant_rank,
                "rankings": rankings,
            }
        )

    return {
        "retrieval_mode": "bm25",
        "model": {
            "model_id": "qdrant/bm25",
            "tokenizer": "multilingual",
            "stemmer": "none",
            "stopwords": [],
        },
        "summary": {
            "case_count": len(cases),
            "positive_case_count": len(reciprocal_ranks),
            "corpus_gap_case_count": len(corpus_gap_results),
            "recall_at_1": _mean(recalls[1]),
            "recall_at_3": _mean(recalls[3]),
            "recall_at_5": _mean(recalls[5]),
            "mrr": _mean(reciprocal_ranks),
            "corpus_gap_empty_rate": _mean(corpus_gap_results),
        },
        "cases": case_results,
    }


def evaluate_rrf_retrieval(
    chunks: Sequence[dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    embedder: Any,
    index: Any,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Evaluate Qdrant reciprocal-rank fusion of dense and BM25 retrieval."""

    document_vectors = embedder.embed_documents([chunk["text"] for chunk in chunks])
    index.build(chunks, document_vectors)

    case_results = []
    reciprocal_ranks = []
    recalls = {1: [], 3: [], 5: []}
    corpus_gap_results = []

    for case in cases:
        hits = index.search(
            HybridQuery(
                text=case["question"],
                dense_vector=embedder.embed_query(case["question"]),
            ),
            mode="rrf",
            filters=case.get("required_filters") or None,
            limit=limit,
        )
        rankings = [
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "primary_evidence_id": hit.primary_evidence_id,
                "physical_pages": hit.physical_pages,
                "usage_policy": hit.usage_policy,
                "score": hit.score,
            }
            for rank, hit in enumerate(hits, start=1)
        ]
        relevant_ids = set(case.get("required_evidence_ids", [])) | set(
            case.get("acceptable_evidence_ids", [])
        )
        first_relevant_rank = next(
            (
                item["rank"]
                for item in rankings
                if item["primary_evidence_id"] in relevant_ids
            ),
            None,
        )
        if relevant_ids:
            reciprocal_ranks.append(
                0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
            )
            for cutoff in recalls:
                recalls[cutoff].append(
                    float(first_relevant_rank is not None and first_relevant_rank <= cutoff)
                )
        elif case.get("expected_outcome", "").startswith("no_answer"):
            corpus_gap_results.append(not rankings)

        case_results.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "expected_outcome": case["expected_outcome"],
                "required_filters": case.get("required_filters", {}),
                "relevant_evidence_ids": sorted(relevant_ids),
                "first_relevant_rank": first_relevant_rank,
                "rankings": rankings,
            }
        )

    return {
        "retrieval_mode": "rrf",
        "model": {
            "dense_model_id": embedder.model_id,
            "dense_model_revision": embedder.model_revision,
            "dense_dimension": embedder.dimension,
            "query_instruction": embedder.query_instruction,
            "sparse_model_id": "qdrant/bm25",
            "fusion": "rrf",
        },
        "summary": {
            "case_count": len(cases),
            "positive_case_count": len(reciprocal_ranks),
            "corpus_gap_case_count": len(corpus_gap_results),
            "recall_at_1": _mean(recalls[1]),
            "recall_at_3": _mean(recalls[3]),
            "recall_at_5": _mean(recalls[5]),
            "mrr": _mean(reciprocal_ranks),
            "corpus_gap_empty_rate": _mean(corpus_gap_results),
        },
        "cases": case_results,
    }


def _mean(values: Sequence[float | bool]) -> float | None:
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)
