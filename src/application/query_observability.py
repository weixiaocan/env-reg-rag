"""Query trace model and replaceable persistence adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.application.answer_generation import ModelUsage


@dataclass(frozen=True)
class QueryTrace:
    query_id: str
    question: str
    caller_type: str
    corpus_version: str
    status: str
    started_at: str
    finished_at: str
    stage_elapsed_ms: dict[str, float]
    resolved_scope: dict[str, str]
    evidence_ids: list[str]
    claim_evidence: dict[str, list[str]]
    model_usage: ModelUsage
    runtime_metadata: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None


class QueryTraceRecorder(Protocol):
    def record(self, trace: QueryTrace) -> None: ...


class NullQueryTraceRecorder:
    def record(self, trace: QueryTrace) -> None:
        return None


class InMemoryQueryTraceRecorder:
    def __init__(self) -> None:
        self.traces: list[QueryTrace] = []

    def record(self, trace: QueryTrace) -> None:
        self.traces.append(trace)
