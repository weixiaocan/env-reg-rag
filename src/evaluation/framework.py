"""Stable contracts for retrieval, generation, and end-to-end evaluation.

The framework deliberately contains no provider or runtime composition.  It
defines the freeze gate and trace shape that concrete evaluation runners must
honour once the application code is ready for a full run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, Sequence


class EvaluationMode(str, Enum):
    RETRIEVAL = "retrieval"
    GENERATION = "generation"
    E2E = "e2e"


class FreezeGateError(ValueError):
    """The requested evaluation run is not reproducible or not authorized."""


class RetrievalPort(Protocol):
    async def retrieve(self, *, question: str, limit: int) -> Sequence[dict[str, Any]]:
        """Return ranked evidence with stable source locators."""


class GoldContextGeneratorPort(Protocol):
    async def generate(
        self,
        *,
        question: str,
        gold_evidence: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate from reviewed evidence while bypassing retrieval."""


class EndToEndAnswerPort(Protocol):
    async def answer(self, *, question: str) -> dict[str, Any]:
        """Run the real query pipeline and return answer plus evidence trace."""


class JudgePort(Protocol):
    async def judge(
        self,
        *,
        question: str,
        expected_facts: Sequence[str],
        evidence: Sequence[dict[str, Any]],
        answer: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a structured verdict; implementations must preserve reasons."""


_REQUIRED_COMPONENTS = {
    EvaluationMode.RETRIEVAL: frozenset({"retriever"}),
    EvaluationMode.GENERATION: frozenset({"gold_context_generator", "judge"}),
    EvaluationMode.E2E: frozenset({"answer_e2e", "judge"}),
}


def required_components(mode: EvaluationMode) -> set[str]:
    return set(_REQUIRED_COMPONENTS[EvaluationMode(mode)])


@dataclass(frozen=True)
class EvaluationRunConfig:
    run_id: str
    modes: tuple[EvaluationMode, ...]
    run_kind: Literal["provisional", "official"]
    dataset_sha256: str
    corpus_version: str
    code_revision: str | None
    worktree_clean: bool
    external_calls: bool = False
    external_data_authorized: bool = False
    retrieve_limit: int = 10
    generate_context_limit: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evaluation-run-config/1.0",
            "run_id": self.run_id,
            "modes": [mode.value for mode in self.modes],
            "run_kind": self.run_kind,
            "dataset_sha256": self.dataset_sha256,
            "corpus_version": self.corpus_version,
            "code_revision": self.code_revision,
            "worktree_clean": self.worktree_clean,
            "external_calls": self.external_calls,
            "external_data_authorized": self.external_data_authorized,
            "retrieve_limit": self.retrieve_limit,
            "generate_context_limit": self.generate_context_limit,
            "metadata": dict(self.metadata),
        }


def validate_run_config(
    config: EvaluationRunConfig,
    *,
    available_components: set[str],
) -> EvaluationRunConfig:
    if config.run_kind not in {"provisional", "official"}:
        raise FreezeGateError("run_kind must be provisional or official")
    if not config.run_id.strip():
        raise FreezeGateError("run_id is required")
    if not config.modes:
        raise FreezeGateError("at least one evaluation mode is required")
    if len(set(config.modes)) != len(config.modes):
        raise FreezeGateError("evaluation modes must be unique")
    if not config.dataset_sha256.strip():
        raise FreezeGateError("dataset_sha256 is required")
    if not config.corpus_version.strip():
        raise FreezeGateError("corpus_version is required")
    if config.retrieve_limit < 10:
        raise FreezeGateError("retrieve_limit must be at least 10")
    if not 1 <= config.generate_context_limit <= config.retrieve_limit:
        raise FreezeGateError(
            "generate_context_limit must be between 1 and retrieve_limit"
        )

    required = set().union(*(required_components(mode) for mode in config.modes))
    missing = sorted(required - available_components)
    if missing:
        raise FreezeGateError(f"missing evaluation components: {', '.join(missing)}")

    if config.external_calls and not config.external_data_authorized:
        raise FreezeGateError(
            "external data authorization is required before model or judge calls"
        )

    if config.run_kind == "official":
        if not config.code_revision or not config.code_revision.strip():
            raise FreezeGateError("official run requires a code revision")
        if not config.worktree_clean:
            raise FreezeGateError("official run worktree must be clean")
    return config


@dataclass(frozen=True)
class EvaluationCaseTrace:
    case_id: str
    mode: EvaluationMode
    question: str
    expected_facts: list[str]
    gold_evidence: list[dict[str, Any]]
    retrieved: list[dict[str, Any]]
    generation_context: list[dict[str, Any]]
    answer: dict[str, Any] | None
    metrics: dict[str, Any]
    judge: dict[str, Any] | None
    timings_ms: dict[str, float]
    failure_attribution: Literal["retrieval", "generation", "both", "ok", "not_applicable"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "evaluation-case-trace/1.0",
            "case_id": self.case_id,
            "mode": self.mode.value,
            "question": self.question,
            "expected_facts": list(self.expected_facts),
            "gold_evidence": list(self.gold_evidence),
            "retrieved": list(self.retrieved),
            "generation_context": list(self.generation_context),
            "answer": self.answer,
            "metrics": dict(self.metrics),
            "judge": self.judge,
            "timings_ms": dict(self.timings_ms),
            "failure_attribution": self.failure_attribution,
        }


def build_case_trace(
    *,
    case_id: str,
    mode: EvaluationMode,
    question: str,
    expected_facts: Sequence[str],
    gold_evidence: Sequence[dict[str, Any]],
    retrieved: Sequence[dict[str, Any]] = (),
    generation_context: Sequence[dict[str, Any]] = (),
    answer: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    judge: dict[str, Any] | None = None,
    timings_ms: dict[str, float] | None = None,
    failure_attribution: Literal[
        "retrieval", "generation", "both", "ok", "not_applicable"
    ] = "not_applicable",
) -> EvaluationCaseTrace:
    return EvaluationCaseTrace(
        case_id=case_id,
        mode=EvaluationMode(mode),
        question=question,
        expected_facts=list(expected_facts),
        gold_evidence=list(gold_evidence),
        retrieved=list(retrieved),
        generation_context=list(generation_context),
        answer=answer,
        metrics=dict(metrics or {}),
        judge=judge,
        timings_ms={key: float(value) for key, value in (timings_ms or {}).items()},
        failure_attribution=failure_attribution,
    )
