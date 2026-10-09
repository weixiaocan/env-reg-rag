"""Contract tests for the retrieval-bypassed gold-evidence diagnostic."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.run_gold_generation_diag import (  # noqa: E402
    build_gold_pack,
    build_summary,
    run_case,
)
from src.application.answer_generation import (  # noqa: E402
    AnswerGenerationResult,
    ModelUsage,
)
from src.domain.query import Answer, Claim  # noqa: E402


def _case(case_id="G001", expected="answer"):
    return {
        "id": case_id,
        "question": "问题？",
        "expected_answerability": expected,
        "expected_facts": ["2027年达到73%"],
    }


def _hit():
    return {
        "chunk_id": "chunk-good",
        "text": "到2027年达到73%以上",
        "file_name": "规范.pdf",
        "physical_pages": [2],
        "heading_path": ["目标"],
        "usage_policy": "answer_and_citation",
    }


def _s2(complete=True):
    return {
        "complete_strict": complete,
        "facts": [{
            "reachable_strong_at_5": complete,
            "bindings": [{"chunk_id": "chunk-good", "supported": complete}],
            "carriers": [{
                "chunk_id": "chunk-good",
                "live_usage_policy": "answer_and_citation",
                "answer_space_rank": 1,
                "supports_fact": complete,
                "exists": True,
                "text": "到2027年达到73%以上",
                "file_name": "规范.pdf",
                "physical_pages": [2],
                "heading_path": ["目标"],
            }],
        }],
    }


def test_build_gold_pack_uses_only_strong_citable_carriers():
    pack, reason = build_gold_pack(
        _case(), _s2(), "corpus-x"
    )
    assert reason is None
    assert [e.evidence_id for e in pack.evidence] == ["chunk-good"]
    assert pack.evidence[0].usage_policy == "answer_and_citation"


def test_build_gold_pack_marks_unready_and_refuse_without_elevation():
    pack, reason = build_gold_pack(
        _case(), _s2(False), "corpus-x"
    )
    assert pack is None
    assert reason == "evidence_preparation_insufficient"
    refuse_pack, reason = build_gold_pack(
        _case("G050", "refuse"), {}, "corpus-x"
    )
    assert reason is None
    assert refuse_pack.evidence == []


def test_unrelated_citable_carrier_cannot_replace_supported_locator_binding():
    s2 = {
        "facts": [{
            "bindings": [{"chunk_id": "chunk-locator", "supported": True}],
            "carriers": [
                {"chunk_id": "chunk-locator", "supports_fact": True,
                 "live_usage_policy": "source_locator_only", "exists": True,
                 "text": "Q为雨水设计流量", "file_name": "规范.pdf"},
                {"chunk_id": "chunk-unrelated", "supports_fact": False,
                 "live_usage_policy": "answer_and_citation", "exists": True,
                 "answer_space_rank": 1, "text": "仅含2km²条件",
                 "file_name": "规范.pdf"},
            ],
        }],
    }
    pack, reason = build_gold_pack(_case(), s2, "corpus-x")
    assert pack is None
    assert reason == "evidence_preparation_insufficient"


def test_supported_citable_binding_is_eligible_regardless_of_retrieval_rank():
    s2 = _s2()
    s2["facts"][0]["carriers"][0]["answer_space_rank"] = 99
    pack, reason = build_gold_pack(_case(), s2, "corpus-x")
    assert reason is None
    assert [item.evidence_id for item in pack.evidence] == ["chunk-good"]


class FakeGenerator:
    async def generate(self, *, request, evidence_pack):
        if evidence_pack.evidence:
            answer = Answer(claims=[Claim(
                claim_id="c1",
                text="到2027年达到73%以上",
                evidence_ids=[evidence_pack.evidence[0].evidence_id],
            )])
        else:
            answer = Answer(claims=[])
        return AnswerGenerationResult(
            answer=answer,
            usage=ModelUsage(input_tokens=10, output_tokens=3, total_tokens=13),
        )


def test_run_case_and_summary_keep_all_denominators():
    pack, _ = build_gold_pack(_case(), _s2(), "corpus-x")
    answered = asyncio.run(run_case(FakeGenerator(), _case(), pack, "run", None))
    insufficient = asyncio.run(run_case(
        FakeGenerator(), _case("G002", "locate"), None, "run",
        "evidence_preparation_insufficient",
    ))
    refuse_pack, _ = build_gold_pack(
        _case("G050", "refuse"), {}, "corpus-x"
    )
    refused = asyncio.run(run_case(
        FakeGenerator(), _case("G050", "refuse"), refuse_pack, "run", None
    ))
    assert answered["status"] == "answered"
    assert answered["citation_problems"] == []
    assert insufficient["model_called"] is False
    assert refused["status"] == "refused"
    summary = build_summary([answered, insufficient, refused])
    assert summary["denominators"]["all_cases"] == 3
    assert summary["denominators"]["model_called"] == 2
    assert summary["denominators"]["evidence_preparation_insufficient"] == 1
    assert summary["token_usage_totals"]["total_tokens"] == 26
