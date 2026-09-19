"""Production composition root for the query application module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient

from src.adapters.llm_provider import LlmProviderSettings, build_answer_generator
from src.adapters.inventory_document_catalog import InventoryDocumentCatalog
from src.adapters.jsonl_query_trace_recorder import JsonlQueryTraceRecorder
from src.adapters.qdrant_evidence_catalog import QdrantEvidenceCatalog
from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.corpus_artifacts import resolve_current_corpus
from src.application.evidence_source import EvidenceSourceService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.server.readiness import QdrantReadinessProbe, ReadinessProbe


@dataclass(frozen=True)
class QueryServices:
    answer: QueryApplicationService
    source_lookup: QueryApplicationService
    documents: InventoryDocumentCatalog
    evidence: EvidenceSourceService
    readiness: ReadinessProbe
    corpus_version: str


def build_query_services(
    *, project_root: Path, trace_output_path: Path | None = None
) -> QueryServices:
    """Wire both safe query profiles without leaking them into HTTP code."""
    settings = LlmProviderSettings.from_env()
    current_corpus = resolve_current_corpus(project_root)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
    )
    answer_generator = build_answer_generator(settings)
    formal_index = QdrantRetrievalIndex(
        client=client,
        collection_name=current_corpus.query_alias,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    numeric_index = NumericRangeIndex.from_artifacts(
        evidence_units_path=current_corpus.evidence_units_path,
        corpus_manifest_path=current_corpus.manifest_path,
    )
    locator_alias = (current_corpus.query_alias if current_corpus.corpus_version != 'formal-corpus-v1'
                     else 'm3_experiment_current')
    source_locator_index = QdrantRetrievalIndex(
        client=client,
        collection_name=locator_alias,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    trace_recorder = JsonlQueryTraceRecorder(
        trace_output_path
        or Path(
            os.getenv(
                "QUERY_TRACE_PATH",
                str(project_root / "data" / "observability" / "query-traces.jsonl"),
            )
        )
    )
    answer_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=formal_index,
            embedder=embedder,
            numeric_range_index=numeric_index,
            fixed_filters={"usage_policy": "answer_and_citation"},
            limit=5,
        ),
        answer_generator=answer_generator,
        trace_recorder=trace_recorder,
        runtime_metadata={
            "provider": settings.provider,
            "model": settings.model,
            "retrieval_profile": "qdrant-bge-small-zh-bm25-rrf+numeric",
            "collection_alias": current_corpus.query_alias,
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "prompt_version": "m4-evidence-answer-v1",
        },
    )
    source_lookup_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=source_locator_index,
            embedder=embedder,
            fixed_filters={"usage_policy": "source_locator_only"},
            limit=5,
        ),
        answer_generator=answer_generator,
        trace_recorder=trace_recorder,
        runtime_metadata={
            "provider": settings.provider,
            "model": settings.model,
            "retrieval_profile": "qdrant-source-locator-only",
            "collection_alias": locator_alias,
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "prompt_version": "not_used_for_source_lookup",
        },
    )
    return QueryServices(
        answer=answer_service,
        source_lookup=source_lookup_service,
        documents=InventoryDocumentCatalog(
            project_root=project_root,
            inventory_path=project_root / "data" / "registry" / "inventory.csv",
        ),
        evidence=EvidenceSourceService(
            QdrantEvidenceCatalog(
                formal_index=formal_index,
                source_locator_index=source_locator_index,
            )
        ),
        readiness=QdrantReadinessProbe(
            client=client,
            required_collections=list(dict.fromkeys([current_corpus.query_alias, locator_alias])),
        ),
        corpus_version=current_corpus.corpus_version,
    )


def build_query_service(*, project_root: Path) -> QueryApplicationService:
    """Backward-compatible composition helper for answer-only callers."""
    return build_query_services(project_root=project_root).answer
