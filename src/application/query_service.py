"""Single application entry point for evidence-constrained questions."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter

from src.application.answer_generation import AnswerGenerator, ModelUsage
from src.application.evidence_retrieval import EvidenceRetriever
from src.application.query_observability import (
    NullQueryTraceRecorder,
    QueryTrace,
    QueryTraceRecorder,
)
from src.application.scope_resolution import ScopeResolver
from src.domain.query import AnswerResult, QueryRequest, QueryStatus


logger = logging.getLogger(__name__)


class CitationValidationError(Exception):
    """A generated claim is not bound to citable evidence in this query."""


class QueryApplicationService:
    def __init__(
        self,
        *,
        scope_resolver: ScopeResolver,
        evidence_retriever: EvidenceRetriever,
        answer_generator: AnswerGenerator,
        trace_recorder: QueryTraceRecorder | None = None,
        runtime_metadata: dict[str, str] | None = None,
    ) -> None:
        self._scope_resolver = scope_resolver
        self._evidence_retriever = evidence_retriever
        self._answer_generator = answer_generator
        self._trace_recorder = trace_recorder or NullQueryTraceRecorder()
        self._runtime_metadata = dict(runtime_metadata or {})

    async def execute(self, request: QueryRequest) -> AnswerResult:
        query_started = perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        observation: dict = {
            "stages": {},
            "usage": ModelUsage(pricing_status="not_used"),
        }
        result: AnswerResult | None = None
        error_type: str | None = None
        try:
            result = await self._execute(request, observation)
            return result
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            observation["stages"]["total"] = round(
                (perf_counter() - query_started) * 1000, 3
            )
            evidence = result.evidence if result is not None else []
            answer = result.answer if result is not None else None
            trace = QueryTrace(
                query_id=request.request_id,
                question=request.question,
                caller_type=request.caller_type,
                corpus_version=(
                    result.corpus_version
                    if result is not None
                    else request.corpus_version
                ),
                status=result.status.value if result is not None else "error",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                stage_elapsed_ms=observation["stages"],
                resolved_scope=result.resolved_scope if result is not None else {},
                evidence_ids=[item.evidence_id for item in evidence],
                claim_evidence={
                    claim.claim_id: list(claim.evidence_ids)
                    for claim in (answer.claims if answer is not None else [])
                },
                model_usage=observation["usage"],
                runtime_metadata=self._runtime_metadata,
                error_type=error_type,
            )
            try:
                self._trace_recorder.record(trace)
            except Exception:
                logger.exception(
                    "query trace persistence failed for query_id=%s",
                    request.request_id,
                )

    async def _execute(self, request: QueryRequest, observation: dict) -> AnswerResult:
        stage_started = perf_counter()
        scope = await self._scope_resolver.resolve(request)
        observation["stages"]["scope"] = round(
            (perf_counter() - stage_started) * 1000, 3
        )
        effective_request = replace(
            request,
            question=scope.effective_question or request.question,
            conversation_context=scope.resolved_scope,
        )
        if scope.missing_conditions:
            return AnswerResult(
                query_id=request.request_id,
                status=QueryStatus.NEEDS_CLARIFICATION,
                resolved_scope=scope.resolved_scope,
                missing_conditions=scope.missing_conditions,
                answer=None,
                evidence=[],
                corpus_version=request.corpus_version,
                continuation_context={
                    "pending_question": scope.anchor_question or request.question,
                    **scope.resolved_scope,
                },
            )
        stage_started = perf_counter()
        evidence_pack = await self._evidence_retriever.retrieve(
            question=effective_request.question,
            resolved_scope=scope.resolved_scope,
            corpus_version=request.corpus_version,
        )
        observation["stages"]["retrieval"] = round(
            (perf_counter() - stage_started) * 1000, 3
        )
        if not evidence_pack.evidence:
            return AnswerResult(
                query_id=request.request_id,
                status=QueryStatus.REFUSED,
                resolved_scope=scope.resolved_scope,
                missing_conditions=[],
                answer=None,
                evidence=[],
                corpus_version=evidence_pack.corpus_version,
                warnings=["no evidence in the selected corpus"],
                continuation_context={
                    "anchor_question": scope.anchor_question or request.question,
                    **scope.resolved_scope,
                },
            )
        if evidence_pack.evidence and all(
            item.usage_policy == "source_locator_only"
            for item in evidence_pack.evidence
        ):
            return AnswerResult(
                query_id=request.request_id,
                status=QueryStatus.SEARCH_ONLY,
                resolved_scope=scope.resolved_scope,
                missing_conditions=[],
                answer=None,
                evidence=evidence_pack.evidence,
                corpus_version=evidence_pack.corpus_version,
                warnings=["evidence transcription is not approved; open the source page"],
                continuation_context={
                    "anchor_question": scope.anchor_question or request.question,
                    **scope.resolved_scope,
                },
            )
        stage_started = perf_counter()
        generation = await self._answer_generator.generate(
            request=effective_request,
            evidence_pack=evidence_pack,
        )
        observation["stages"]["generation"] = round(
            (perf_counter() - stage_started) * 1000, 3
        )
        observation["usage"] = generation.usage
        answer = generation.answer
        if not answer.claims and not answer.conflicts:
            return AnswerResult(
                query_id=request.request_id,
                status=QueryStatus.REFUSED,
                resolved_scope=scope.resolved_scope,
                missing_conditions=[],
                answer=None,
                evidence=evidence_pack.evidence,
                corpus_version=evidence_pack.corpus_version,
                warnings=["retrieved evidence does not support an answer"],
                continuation_context={
                    "anchor_question": scope.anchor_question or request.question,
                    **scope.resolved_scope,
                },
            )
        stage_started = perf_counter()
        citable_ids = {
            item.evidence_id
            for item in evidence_pack.evidence
            if item.usage_policy == "answer_and_citation"
        }
        for claim in answer.claims:
            if not claim.evidence_ids or not set(claim.evidence_ids).issubset(citable_ids):
                raise CitationValidationError(
                    f"claim {claim.claim_id} is not supported by citable evidence"
                )
        claimed_evidence_ids = {
            evidence_id
            for claim in answer.claims
            for evidence_id in claim.evidence_ids
        }
        evidence_by_id = {
            item.evidence_id: item
            for item in evidence_pack.evidence
            if item.usage_policy == "answer_and_citation"
        }
        for conflict in answer.conflicts:
            conflict_ids = set(conflict.evidence_ids)
            if len(conflict_ids) < 2 or not conflict_ids.issubset(citable_ids):
                raise CitationValidationError(
                    f"conflict {conflict.conflict_id} is not supported by citable evidence"
                )
            document_versions = {
                evidence_by_id[evidence_id].document_version_id
                for evidence_id in conflict_ids
            }
            if len(document_versions) < 2:
                raise CitationValidationError(
                    f"conflict {conflict.conflict_id} must compare different document versions"
                )
            if not conflict_ids.issubset(claimed_evidence_ids):
                raise CitationValidationError(
                    f"conflict {conflict.conflict_id} must explain every conflicting source"
                )
        status = (
            QueryStatus.CONFLICTING_SOURCES
            if answer.conflicts
            else QueryStatus.ANSWERED
        )
        observation["stages"]["validation"] = round(
            (perf_counter() - stage_started) * 1000, 3
        )
        return AnswerResult(
            query_id=request.request_id,
            status=status,
            resolved_scope=scope.resolved_scope,
            missing_conditions=[],
            answer=answer,
            evidence=evidence_pack.evidence,
            corpus_version=evidence_pack.corpus_version,
            warnings=["applicable sources contain incompatible requirements"]
            if answer.conflicts
            else [],
            continuation_context={
                "anchor_question": scope.anchor_question or request.question,
                **scope.resolved_scope,
            },
        )
