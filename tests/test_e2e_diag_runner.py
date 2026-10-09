"""Contract tests for the end-to-end diagnostic runner (fake service only).

The real run in eval/runs/diagnostic-e2e-*/ is a separate authorized action;
these tests pin the HTTP-entry recording, trace merge and summary logic
against a fake query application service -- no Qdrant, no model, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from scripts.run_e2e_diag import (  # noqa: E402
    build_summary,
    classify_outcome,
    merge_traces,
    run_e2e_case,
    run_e2e_cases,
)
from src.domain.query import (  # noqa: E402
    Answer,
    AnswerResult,
    Claim,
    EvidenceItem,
    QueryStatus,
)
from src.server.fastapi_app import create_app  # noqa: E402


def _evidence(eid: str, policy: str = "answer_and_citation") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        text="屋面径流系数取0.85~0.95",
        document_version_id="dv-1",
        file_name="规范.pdf",
        physical_pages=[12],
        heading_path=["4"],
        usage_policy=policy,
        source_uri="local://规范.pdf#p12",
    )


def _answered() -> AnswerResult:
    return AnswerResult(
        query_id="q",
        status=QueryStatus.ANSWERED,
        resolved_scope={},
        missing_conditions=[],
        answer=Answer(
            claims=[Claim(claim_id="claim-1", text="径流系数0.85~0.95",
                          evidence_ids=["ev-1"])],
            conflicts=[],
        ),
        evidence=[_evidence("ev-1")],
        corpus_version="corpus-x",
        warnings=[],
    )


class FakeService:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        import dataclasses

        return dataclasses.replace(self._result, query_id=request.request_id)


def _client(service) -> TestClient:
    return TestClient(
        create_app(
            query_service=service,
            answer_corpus_version="corpus-x",
            query_rate_limiter=None,
        ),
        raise_server_exceptions=False,
    )


class TestE2ECase:
    def test_answered_roundtrip(self):
        from src.domain.query import QueryStatus

        service = FakeService(result=_answered())
        client = _client(service)
        case = {"id": "G009", "question": "Q?",
                "expected_answerability": "answer"}
        record = run_e2e_case(client, case, "runE")
        assert record["http_status"] == 200
        assert record["status"] == "answered"
        assert record["evidence_ids"] == ["ev-1"]
        assert record["retrieved_evidence"][0]["evidence_id"] == "ev-1"
        assert record["answer"]["claims"][0]["claim_id"] == "claim-1"
        assert record["wall_ms"] > 0
        assert service.requests[0].request_id == "runE-G009"
        assert "failure_class" not in record

    def test_service_error_maps_to_http_failure_class(self):
        from src.application.query_service import CitationValidationError

        service = FakeService(error=CitationValidationError("bad claim"))
        client = _client(service)
        case = {"id": "G010", "question": "Q?",
                "expected_answerability": "locate"}
        record = run_e2e_case(client, case, "runE")
        assert record["http_status"] == 500
        assert record["failure_class"] == "http_server_error"
        assert record["status"] is None


class TestRunLoopAndSummary:
    def test_loop_and_summary(self):
        from src.domain.query import QueryStatus

        service = FakeService(result=_answered())
        client = _client(service)
        cases = [
            {"id": "G009", "question": "Q1?", "expected_answerability": "answer"},
            {"id": "G010", "question": "Q2?", "expected_answerability": "locate"},
        ]
        records = run_e2e_cases(client, cases, "runE")
        assert [r["case"] for r in records] == ["G009", "G010"]
        summary = build_summary(records)
        assert summary["status_distribution"] == {"answered": 2}
        assert summary["latency_ms"]["wall"]["n"] == 2
        assert summary["trace_missing_count"] == 2  # no trace file merged yet
        assert summary["token_usage_totals"]["input_tokens"] == 0

    def test_failure_attribution_distinguishes_admission_and_generation(self):
        base = {"expected_answerability": "answer", "failure_class": None}
        assert classify_outcome({**base, "status": "search_only"}) == "evidence_admission"
        assert classify_outcome({**base, "status": "refused", "generate_context_evidence_ids": []}) == "retrieval_or_admission_refusal"
        assert classify_outcome({**base, "status": "refused", "generate_context_evidence_ids": ["ev-1"]}) == "generation_refusal"
        assert classify_outcome({"expected_answerability": "refuse", "status": "refused", "failure_class": None}) == "correct_refusal"

    def test_merge_traces_fills_stages_and_usage(self, tmp_path):
        from src.domain.query import QueryStatus

        trace_path = tmp_path / "query-traces.jsonl"
        trace_path.write_text(
            json.dumps(
                {
                    "query_id": "runE-G009",
                    "stage_elapsed_ms": {"scope": 1.0, "retrieval": 25.0,
                                         "generation": 900.0, "total": 930.0},
                    "generate_context_evidence_ids": ["ev-1"],
                    "evidence_ids": ["ev-1", "ev-2"],
                    "model_usage": {"input_tokens": 120, "output_tokens": 60,
                                    "total_tokens": 180,
                                    "pricing_status": "not_configured"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        service = FakeService(result=_answered())
        client = _client(service)
        record = run_e2e_case(
            client,
            {"id": "G009", "question": "Q?", "expected_answerability": "answer"},
            "runE",
        )
        merge_traces([record], trace_path)
        assert record["stages_ms"]["generation"] == 900.0
        assert record["token_usage"]["total_tokens"] == 180
        assert record["trace_evidence_ids"] == ["ev-1", "ev-2"]
        summary = build_summary([record])
        assert summary["trace_missing_count"] == 0
        assert summary["latency_ms"]["generation_stage"]["p50"] == 900.0
