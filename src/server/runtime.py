"""Production composition root for the V2 query application module.

Wires the published corpus's Qdrant collection (from the current pointer) to
both query profiles
(answer / source_lookup). The answer profile uses the LangChain generator
backed by the provider configured in ``.env`` (default: zhipu GLM). When the
LLM is not configured (missing API key, unsupported provider), retrieval
remains available and the answer profile explicitly reports that generation
is unavailable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient

from src.adapters.inventory_document_catalog import DocumentCatalog
from src.adapters.jsonl_query_trace_recorder import JsonlQueryTraceRecorder
from src.adapters.pdf_region_renderer import DocumentPageImageService, RegionImageService
from src.adapters.llm_provider import (
    LlmConfigurationError,
    LlmProviderSettings,
    build_answer_generator,
    build_question_rewriter,
)
from src.adapters.qdrant_evidence_catalog import QdrantEvidenceCatalog
from src.adapters.qdrant_evidence_retriever import (
    QdrantContextExpander,
    QdrantEvidenceRetriever,
)
from src.adapters.public_document_catalog import PublicDocumentCatalog
from src.application.answer_generation import Answer, AnswerGenerator, InMemoryAnswerGenerator
from src.application.question_rewriting import QuestionRewriter
from src.application.corpus_artifacts import (
    resolve_current_corpus,
    resolve_query_collection,
)
from src.application.evidence_source import EvidenceSourceService
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import PassthroughScopeResolver
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.server.readiness import QdrantReadinessProbe, ReadinessProbe
from src.server.rate_limit import QueryRateLimiter, build_query_rate_limiter

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryServices:
    answer: QueryApplicationService
    source_lookup: QueryApplicationService
    documents: DocumentCatalog
    document_page: DocumentPageImageService
    evidence: EvidenceSourceService
    readiness: ReadinessProbe
    region_image: RegionImageService
    corpus_version: str
    query_rate_limiter: QueryRateLimiter | None
    answer_available: bool


def _build_answer_generator() -> tuple[AnswerGenerator, bool, QuestionRewriter | None]:
    """Build the LangChain answer generator and report availability.

    When ``.env`` does not configure a usable provider (missing key, unknown
    provider), the service must still start: ``source_lookup`` is unaffected
    and ``answer`` queries expose retrieved sources with a configuration warning.
    """
    try:
        settings = LlmProviderSettings.from_env()
    except LlmConfigurationError as exc:
        _log.warning("LLM not configured; answer generation unavailable: %s", exc)
        return InMemoryAnswerGenerator(answer=Answer(claims=[], conflicts=[])), False, None
    return build_answer_generator(settings), True, build_question_rewriter(settings)


def build_query_services(
    *, project_root: Path, trace_output_path: Path | None = None
) -> QueryServices:
    """Wire both safe query profiles against the single V2 collection."""
    current_corpus = resolve_current_corpus(project_root)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
    )
    collection = resolve_query_collection(
        project_root,
        env_override=os.getenv("QDRANT_COLLECTION"),
    )
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=collection,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    answer_generator, answer_available, question_rewriter = _build_answer_generator()
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
        scope_resolver=PassthroughScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=index,
            embedder=embedder,
            # Unreviewed transcriptions may answer with a per-claim caveat
            # (owner decision 2026-10-12, reconfirmed 2026-10-16); excluded
            # chunks never answer.
            fixed_filters={
                "usage_policy": ["answer_and_citation", "source_locator_only"]
            },
            limit=10,
        ),
        answer_generator=answer_generator,
        question_rewriter=question_rewriter,
        trace_recorder=trace_recorder,
        generate_context_limit=5,
        allow_unverified_evidence=True,
        generation_unavailable_reason=(
            None if answer_available else "AI 回答暂不可用：服务器未配置智谱模型密钥；已为你找到原文。"
        ),
        context_expander=QdrantContextExpander(
            index=index,
            allowed_policies={"answer_and_citation", "source_locator_only"},
        ),
        runtime_metadata={
            "retrieval_profile": "qdrant-bge-small-zh-bm25-rrf",
            "collection": collection,
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "generation": "langchain-zhipu-glm-inline-cite",
            "follow_up": "history-rewrite-then-retrieve",
            "retrieve_limit": "10",
            "generate_context_limit": "5",
            "evidence_tiers": "verified+unverified-caveat",
            "context_expansion": "structured-clause-v2",
        },
    )
    source_lookup_service = QueryApplicationService(
        scope_resolver=PassthroughScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=index,
            embedder=embedder,
            fixed_filters={},
            limit=10,
        ),
        answer_generator=answer_generator,
        trace_recorder=trace_recorder,
        skip_generation=True,
        runtime_metadata={
            "retrieval_profile": "qdrant-bge-small-zh-bm25-rrf",
            "collection": collection,
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "generation": "skipped-source-lookup",
            "retrieve_limit": "10",
        },
    )
    documents = PublicDocumentCatalog(
        project_root=project_root,
        inventory_path=project_root / "data" / "registry" / "inventory.csv",
    )
    return QueryServices(
        answer=answer_service,
        source_lookup=source_lookup_service,
        documents=documents,
        document_page=DocumentPageImageService(documents),
        evidence=EvidenceSourceService(QdrantEvidenceCatalog(index=index)),
        readiness=QdrantReadinessProbe(
            client=client,
            query_entry=collection,
            expected_alias=current_corpus.alias,
            expected_collection=current_corpus.collection_name,
            expected_point_count=current_corpus.chunk_count,
        ),
        region_image=RegionImageService(index=index, document_catalog=documents),
        corpus_version=current_corpus.corpus_version,
        query_rate_limiter=build_query_rate_limiter(),
        answer_available=answer_available,
    )


def build_query_service(*, project_root: Path) -> QueryApplicationService:
    """Backward-compatible composition helper for answer-only callers."""
    return build_query_services(project_root=project_root).answer
