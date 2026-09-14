"""Shared transport DTOs for HTTP and MCP query adapters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.query import QueryStatus


class ClaimDto(BaseModel):
    claim_id: str
    text: str
    evidence_ids: list[str]


class ConflictDto(BaseModel):
    conflict_id: str
    description: str
    evidence_ids: list[str]


class AnswerDto(BaseModel):
    claims: list[ClaimDto]
    conflicts: list[ConflictDto] = Field(default_factory=list)


class EvidenceDto(BaseModel):
    evidence_id: str
    text: str
    document_version_id: str
    file_name: str
    physical_pages: list[int]
    heading_path: list[str]
    usage_policy: str
    source_uri: str
    jurisdiction: str
    document_kind: str
    effective_status: str
    source_authority: str
    publication_date: str
    effective_from: str
    effective_to: str


class AnswerResultDto(BaseModel):
    query_id: str
    status: QueryStatus
    resolved_scope: dict[str, str]
    missing_conditions: list[str]
    answer: AnswerDto | None
    evidence: list[EvidenceDto]
    corpus_version: str
    warnings: list[str]
    continuation_context: dict[str, str]
