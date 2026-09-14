"""Domain types for evidence-constrained queries."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class QueryStatus(StrEnum):
    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    SEARCH_ONLY = "search_only"
    CONFLICTING_SOURCES = "conflicting_sources"
    REFUSED = "refused"


@dataclass(frozen=True)
class QueryRequest:
    request_id: str
    question: str
    conversation_context: dict[str, str]
    corpus_version: str
    caller_type: str


@dataclass(frozen=True)
class ScopeResolution:
    resolved_scope: dict[str, str] = field(default_factory=dict)
    missing_conditions: list[str] = field(default_factory=list)
    effective_question: str = ""
    anchor_question: str = ""


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    text: str
    document_version_id: str
    file_name: str
    physical_pages: list[int]
    heading_path: list[str]
    usage_policy: str
    source_uri: str
    jurisdiction: str = "unknown"
    document_kind: str = "unknown"
    effective_status: str = "unknown"
    source_authority: str = ""
    publication_date: str = ""
    effective_from: str = ""
    effective_to: str = ""


@dataclass(frozen=True)
class EvidencePack:
    corpus_version: str
    evidence: list[EvidenceItem]


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    description: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class Answer:
    claims: list[Claim]
    conflicts: list[Conflict] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerResult:
    query_id: str
    status: QueryStatus
    resolved_scope: dict[str, str]
    missing_conditions: list[str]
    answer: Answer | None
    evidence: list[EvidenceItem]
    corpus_version: str
    warnings: list[str] = field(default_factory=list)
    continuation_context: dict[str, str] = field(default_factory=dict)
