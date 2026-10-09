"""Contract tests for the generation diagnostic runner (fake model only).

Per AGENTS.md section 5: local fake-model/contract tests need NO external
LLM authorization.  The real run in eval/runs/diagnostic-gen-*/ is a separate,
authorized action -- these tests pin the runner's recording, judgment and
summary logic without Qdrant or a model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.run_generation_diag import (  # noqa: E402
    RecordingAnswerGenerator,
    _binding_problems,
    _claim_support_problems,
    _refusal_stage,
    build_summary,
    judge_case,
    merge_traces,
    run_cases,
)
from src.application.answer_generation import (  # noqa: E402
    AnswerGenerationResult,
    ModelUsage,
)
from src.domain.query import (  # noqa: E402
    Answer,
    AnswerResult,
    Claim,
    EvidenceItem,
    EvidencePack,
    QueryRequest,
    QueryStatus,
)
from src.application.query_service import CitationValidationError  # noqa: E402


def _evidence(eid: str, text: str = "屋面径流系数取0.85~0.95") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        text=text,
        document_version_id="dv-1",
        file_name="规范.pdf",
        physical_pages=[12],
        heading_path=["4", "4.1"],
        usage_policy="answer_and_citation",
        source_uri="local://规范.pdf#p12",
    )


def _result(
    status: QueryStatus,
    claims: list[Claim] | None = None,
    evidence: list[EvidenceItem] | None = None,
) -> AnswerResult:
    return AnswerResult(
        query_id="q",
        status=status,
        resolved_scope={},
        missing_conditions=[],
        answer=Answer(claims=claims or [], conflicts=[]),
        evidence=evidence or [],
        corpus_version="corpus-x",
        warnings=[],
    )


class FakeService:
    """Duck-typed QueryApplicationService: canned results / raised errors."""

    def __init__(self, behavior):
        self._behavior = behavior
        self.requests: list[QueryRequest] = []

    async def execute(self, request: QueryRequest) -> AnswerResult:
        self.requests.append(request)
        return self._behavior(request)


class RaisingService(FakeService):
    def __init__(self, behavior, raise_for: set[str]):
        super().__init__(behavior)
        self._raise_for = raise_for

    async def execute(self, request: QueryRequest) -> AnswerResult:
        self.requests.append(request)
        if request.request_id.split("-")[-1] in self._raise_for:
            # real query-service error class: the runner classifies citation
            # violations via isinstance on the real class.
            raise CitationValidationError(
                "claim claim-1 is not supported by citable evidence"
            )
        return self._behavior(request)


def _case(case_id: str, expected: str, facts: list[str] | None = None) -> dict:
    return {
        "id": case_id,
        "question": f"问题{case_id}",
        "expected_answerability": expected,
        "anchor_corpus": "corpus-x",
        "expected_facts": facts or [],
        "evidence": [],
        "labels": [],
        "status": "reviewed",
        "doc_coverage": {},
    }


class TestRunCasesAndJudgment:
    def test_statuses_recorded_and_refuse_judged(self):
        behavior = {
            "G042": _result(QueryStatus.REFUSED),  # refuse case, correct
            "G009": _result(
                QueryStatus.ANSWERED,
                claims=[Claim(claim_id="claim-1", text="径流系数0.85~0.95",
                              evidence_ids=["ev-1"])],
                evidence=[_evidence("ev-1")],
            ),
            "G001": _result(QueryStatus.SEARCH_ONLY,
                            evidence=[EvidenceItem(
                                evidence_id="ev-loc", text="定位",
                                document_version_id="dv", file_name="f.pdf",
                                physical_pages=[1], heading_path=[],
                                usage_policy="source_locator_only",
                                source_uri="u")]),
        }
        service = FakeService(lambda req: behavior[req.request_id.split("-")[-1]])
        cases = [
            _case("G042", "refuse"),
            _case("G009", "answer", ["径流系数取0.85~0.95"]),
            _case("G001", "locate"),
        ]
        records = run_cases(
            type("S", (), {"answer": service})(), cases, "corpus-x", "runX"
        )
        assert [r["status"] for r in records] == ["refused", "answered", "search_only"]
        assert _refusal_stage(records[2]) == "search_only_profile"
        # refusal_stage is finalized from trace context: a REFUSED result with
        # generate_context ids means generation ran and refused (empty claims)
        records[0]["generate_context_evidence_ids"] = ["ev-1"]
        assert _refusal_stage(records[0]) == "refused_by_generation"
        records[0]["generate_context_evidence_ids"] = []
        assert _refusal_stage(records[0]) == "refused_at_retrieval"
        judgments = [judge_case(c, r) for c, r in zip(cases, records)]
        assert judgments[0]["refuse_correct"] is True
        assert judgments[1]["answered"] is True
        # fact coverage: claim text contains the fact's numbers
        cov = judgments[1]["expected_facts_coverage"][0]
        assert cov["covered"] is True
        assert "expected" not in judgments[2] or judgments[2].get(
            "answered"
        ) is None  # locate: observed, not judged

    def test_citation_error_recorded_and_loop_continues(self):
        service = RaisingService(
            lambda req: _result(QueryStatus.REFUSED), raise_for={"G009"}
        )
        cases = [_case("G009", "answer"), _case("G010", "locate")]
        records = run_cases(
            type("S", (), {"answer": service})(), cases, "corpus-x", "runX"
        )
        assert records[0]["failure_class"] == "citation_validation_error"
        assert records[0]["status"] is None
        assert records[1]["failure_class"] is None  # loop continued
        assert records[1]["status"] == "refused"


class TestRecordingGenerator:
    def test_captures_prompt_identity_and_delegates(self):
        class Inner:
            def __init__(self):
                self.seen = None

            async def generate(self, *, request, evidence_pack):
                self.seen = (request, evidence_pack)
                return AnswerGenerationResult(
                    answer=Answer(claims=[]),
                    usage=ModelUsage(input_tokens=11, output_tokens=3,
                                     total_tokens=14),
                )

        inner = Inner()
        rec = RecordingAnswerGenerator(inner)
        request = QueryRequest(
            request_id="runX-G009", question="Q", conversation_context={"a": "b"},
            corpus_version="corpus-x", caller_type="evaluation",
        )
        pack = EvidencePack(corpus_version="corpus-x", evidence=[_evidence("ev-1")])
        result = rec.generate_sync_hack(request, pack) if False else None
        import asyncio

        result = asyncio.run(
            rec.generate(request=request, evidence_pack=pack)
        )
        assert result.usage.total_tokens == 14  # delegation verbatim
        assert len(rec.captures) == 1
        cap = rec.captures[0]
        assert cap["request_id"] == "runX-G009"
        assert cap["question_sent"] == "Q"
        assert [e["evidence_id"] for e in cap["prompt_evidence"]] == ["ev-1"]
        assert inner.seen[1] is pack


class TestBindings:
    def test_anchor_mismatch_blocks(self):
        cases = [_case("G001", "locate")]
        problems = _binding_problems(
            cases, {"corpus_version": "corpus-other"}
        )
        assert problems and "G001" in problems[0]

    def test_matching_anchor_passes(self):
        cases = [_case("G001", "locate")]
        assert _binding_problems(cases, {"corpus_version": "corpus-x"}) == []


class TestTraceMergeAndSummary:
    def test_merge_and_summary_counts(self, tmp_path):
        trace_path = tmp_path / "query-traces.jsonl"
        trace_path.write_text(
            json.dumps(
                {
                    "query_id": "runX-G009",
                    "stage_elapsed_ms": {"retrieval": 20.0, "generation": 800.0,
                                         "total": 830.0},
                    "generate_context_evidence_ids": ["ev-1"],
                    "model_usage": {"input_tokens": 100, "output_tokens": 50,
                                    "total_tokens": 150,
                                    "pricing_status": "not_configured"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        records = [
            {
                "case": "G009", "status": "answered",
                "expected_answerability": "answer",
                "stages_ms": {}, "token_usage": {},
            },
            {
                "case": "G042", "status": "refused",
                "expected_answerability": "refuse",
                "stages_ms": {}, "token_usage": {},
            },
        ]
        merge_traces(records, trace_path, "runX")
        assert records[0]["stages_ms"]["generation"] == 800.0
        assert records[0]["token_usage"]["total_tokens"] == 150
        assert "trace_missing" not in records[0]
        assert records[1].get("trace_missing") is True

        cases = [_case("G009", "answer"), _case("G042", "refuse")]
        judgments = [judge_case(c, r) for c, r in zip(cases, records)]
        summary = build_summary(cases, records, judgments)
        assert summary["status_distribution"] == {"answered": 1, "refused": 1}
        assert summary["refusal"]["denominator"] == 1
        assert summary["refusal"]["refuse_correct"] == 1
        assert summary["answer_expected"]["denominator"] == 1
        assert summary["latency_ms"]["generation"]["p50"] == 800.0
        assert summary["token_usage_totals"]["input_tokens"] == 100


class TestClaimSupport:
    def test_numbers_missing_from_cited_evidence_flagged(self):
        record = {
            "answer": {"claims": [
                {"claim_id": "claim-1", "text": "暴雨强度取 99.9 毫米",
                 "evidence_ids": ["ev-1"]},
            ]},
            "prompt_input": {"prompt_evidence": [
                {"evidence_id": "ev-1", "text": "屋面径流系数取0.85~0.95"},
            ]},
        }
        problems = _claim_support_problems(record)
        assert problems and "99.9" in problems[0]

    def test_numbers_present_passes(self):
        record = {
            "answer": {"claims": [
                {"claim_id": "claim-1", "text": "径流系数 0.85~0.95",
                 "evidence_ids": ["ev-1"]},
            ]},
            "prompt_input": {"prompt_evidence": [
                {"evidence_id": "ev-1", "text": "各种屋面 0.85~0.95"},
            ]},
        }
        assert _claim_support_problems(record) == []

    def test_document_number_in_prompt_metadata_is_supported(self):
        record = {
            "answer": {"claims": [
                {"claim_id": "claim-1", "text": "GB/T 47884规定了点位原则",
                 "evidence_ids": ["ev-1"]},
            ]},
            "prompt_input": {"prompt_evidence": [
                {"evidence_id": "ev-1", "text": "在线监测点位布设原则",
                 "file_name": "GBT+47884-2026.pdf", "heading_path": []},
            ]},
        }
        assert _claim_support_problems(record) == []
