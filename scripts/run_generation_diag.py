"""50-case generation diagnostic over the production answer path.

Milestone-2 of the evaluation handoff (EVALUATION_HANDOFF.md): REAL LLM
generation (zhipu GLM via the already-wired LangChainAnswerGenerator) on the
SAME 50 golden cases, through the EXACT production composition
(``src/server/runtime.py::build_query_services().answer``).

DIAGNOSTIC only.  Read-only w.r.t. the golden set, corpus, pointer, quality
statuses and --formal gates.  "检索到内容" ≠ "生成可回答": this run measures
what the generation layer does with whatever the answer profile admits
(answer_and_citation top-10, generation context = first 5).

Per case we save: prompt input identity (question + the exact evidence
allowed into the prompt), structured response (claims/conflicts with bound
evidence IDs), claim-citation support (enforced by the service's citation
validation), refusal/clarification/conflict path, token usage, stage
latencies (from the production query trace), and failure classification.

Usage (real run, authorized provider only):
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/run_generation_diag.py \
        --run-id diagnostic-gen-20261007
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from src.adapters.llm_provider import (  # noqa: E402
    LlmProviderSettings,
    build_answer_generator,
)
from src.application.answer_generation import (  # noqa: E402
    AnswerGenerationResult,
    Answer,
    AnswerGenerator,
    ModelUsage,
)
from src.domain.query import (  # noqa: E402
    AnswerResult,
    EvidencePack,
    QueryRequest,
    QueryStatus,
)
from src.application.query_service import CitationValidationError  # noqa: E402
from src.server import runtime as server_runtime  # noqa: E402

DATASET = ROOT / "eval" / "golden_v7.jsonl"
POINTER = ROOT / "data" / "registry" / "corpus-current.json"
REFUSE_EXPECTED = {"refuse"}
ANSWER_EXPECTED = {"answer"}
# The question's premise is not in the sources: a refusal and an answer
# that corrects the premise from the sources are both acceptable; an
# answer still needs a human check that it invents no figure.
PREMISE_EXPECTED = {"correct_premise"}


def _dataset_sha256(dataset: Path = DATASET) -> str:
    return hashlib.sha256(dataset.read_bytes()).hexdigest()


def _binding_problems(cases: list[dict], pointer: dict) -> list[str]:
    """Fail-closed corpus bindings: golden anchors must match the pointer."""
    problems: list[str] = []
    version = pointer.get("corpus_version")
    if not version:
        problems.append("pointer has no corpus_version")
    for case in cases:
        anchor = case.get("anchor_corpus")
        if anchor != version:
            problems.append(
                f"{case['id']} anchored to {anchor!r} but pointer is {version!r}"
            )
    return problems


class RecordingAnswerGenerator(AnswerGenerator):
    """Wraps the real generator and records the exact prompt inputs.

    The prompt identity is what the handoff requires per case: the question
    sent, the conversation context, and the evidence actually allowed into
    the prompt (evidence IDs + texts + policies).  Delegation stays verbatim.
    """

    def __init__(self, inner: AnswerGenerator) -> None:
        self._inner = inner
        self.captures: list[dict] = []

    async def generate(
        self, *, request: QueryRequest, evidence_pack: EvidencePack
    ) -> AnswerGenerationResult:
        self.captures.append(
            {
                "request_id": request.request_id,
                "question_sent": request.question,
                "conversation_context": dict(request.conversation_context),
                "prompt_evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "usage_policy": item.usage_policy,
                        "file_name": item.file_name,
                        "physical_pages": list(item.physical_pages),
                        "text": item.text,
                    }
                    for item in evidence_pack.evidence
                ],
            }
        )
        return await self._inner.generate(
            request=request, evidence_pack=evidence_pack
        )


def _status_name(status: QueryStatus) -> str:
    return status.value if isinstance(status, QueryStatus) else str(status)


def _refusal_stage(record: dict) -> str:
    """Which stage produced the refusal/clarification (failure taxonomy)."""
    status = record["status"]
    if status == "needs_clarification":
        return "scope_clarification"
    if status == "search_only":
        return "search_only_profile"
    if status == "refused":
        ctx = record.get("generate_context_evidence_ids") or []
        if not record.get("evidence_ids") and not ctx:
            return "refused_at_retrieval"
        if not ctx:
            return "refused_all_locator"
        return "refused_by_generation"
    return "n/a"


def _fact_numbers(text: str) -> list[str]:
    import re

    return re.findall(r"\d+(?:\.\d+)?", text)


def _claim_support_problems(record: dict) -> list[str]:
    """Mechanical claim support check beyond the service's ID-subset gate:
    every claim's own numbers must appear in the union of its cited evidence
    texts (when the claim contains numbers at all)."""
    problems: list[str] = []
    claims = (record.get("answer") or {}).get("claims") or []
    prompt_evidence = {
        e["evidence_id"]: " ".join(
            [
                e.get("text", ""),
                e.get("file_name", ""),
                " ".join(e.get("heading_path") or []),
            ]
        )
        for e in (record.get("prompt_input") or {}).get("prompt_evidence", [])
    }
    for claim in claims:
        cited = " ".join(prompt_evidence.get(cid, "") for cid in claim["evidence_ids"])
        missing = [
            n for n in _fact_numbers(claim["text"]) if n not in cited
        ]
        if missing:
            problems.append(
                f"{claim['claim_id']}: numbers {missing} not in cited evidence"
            )
    return problems


def _expected_facts_coverage(case: dict, record: dict) -> dict:
    """Golden expected_facts vs generated claim texts (number-level)."""
    claims_text = " ".join(
        c["text"] for c in (record.get("answer") or {}).get("claims") or []
    )
    rows = []
    for i, fact in enumerate(case.get("expected_facts", [])):
        nums = _fact_numbers(fact)
        covered = all(n in claims_text for n in nums) if nums else None
        rows.append({"fact_index": i, "numbers": nums, "covered": covered})
    return rows


async def run_case(service, case: dict, corpus_version: str, run_id: str) -> dict:
    request = QueryRequest(
        request_id=f"{run_id}-{case['id']}",
        question=case["question"],
        conversation_context={},
        corpus_version=corpus_version,
        caller_type="evaluation",
    )
    record: dict = {
        "case": case["id"],
        "question": case["question"],
        "expected_answerability": case["expected_answerability"],
        "status": None,
        "answer": None,
        "evidence_ids": [],
        "generate_context_evidence_ids": [],
        "missing_conditions": [],
        "warnings": [],
        "failure_class": None,
        "error_type": None,
    }
    try:
        result: AnswerResult = await service.execute(request)
        record["status"] = _status_name(result.status)
        record["evidence_ids"] = [e.evidence_id for e in result.evidence]
        record["evidence_policies"] = sorted(
            {e.usage_policy for e in result.evidence}
        )
        record["missing_conditions"] = list(result.missing_conditions)
        record["warnings"] = list(result.warnings)
        if result.answer is not None:
            record["answer"] = {
                "claims": [
                    {
                        "claim_id": c.claim_id,
                        "text": c.text,
                        "evidence_ids": list(c.evidence_ids),
                        "verified": c.verified,
                    }
                    for c in result.answer.claims
                ],
                "conflicts": [
                    {
                        "conflict_id": cf.conflict_id,
                        "description": cf.description,
                        "evidence_ids": list(cf.evidence_ids),
                    }
                    for cf in result.answer.conflicts
                ],
            }
    except Exception as exc:  # noqa: BLE001 -- record and continue
        record["error_type"] = type(exc).__name__
        record["failure_class"] = (
            "citation_validation_error"
            if isinstance(exc, CitationValidationError)
            else "generation_error"
        )
        record["error_message"] = str(exc)[:500]
    # refusal_stage is finalized AFTER trace merge (it needs the trace's
    # generate_context ids: REFUSED results carry no evidence on any path).
    record["refusal_stage"] = None
    return record


def judge_case(case: dict, record: dict) -> dict:
    expected = case["expected_answerability"]
    status = record["status"] or "error"
    judgment: dict = {"case": case["id"], "expected": expected, "status": status}
    if expected in REFUSE_EXPECTED:
        judgment["refuse_correct"] = status == "refused"
    elif expected in PREMISE_EXPECTED:
        judgment["acceptable_status"] = status in {
            "refused", "answered", "conflicting_sources"
        }
        judgment["needs_semantic_review"] = status in {
            "answered", "conflicting_sources"
        }
    elif expected in ANSWER_EXPECTED:
        judgment["answered"] = status in {"answered", "conflicting_sources"}
        judgment["claims"] = len(
            (record.get("answer") or {}).get("claims") or []
        )
        judgment["claim_support_problems"] = _claim_support_problems(record)
        judgment["expected_facts_coverage"] = _expected_facts_coverage(case, record)
    # locate (golden_v3 and earlier only): no pass/fail -- it described the
    # evidence admission of its time, not the expected behaviour.
    return judgment


def run_cases(services, cases: list[dict], corpus_version: str, run_id: str) -> list[dict]:
    """Injectable core: `services` only needs `.answer.execute` (duck-typed),
    so contract tests run against fakes without Qdrant or a model."""

    async def _all() -> list[dict]:
        return [
            await run_case(services.answer, case, corpus_version, run_id)
            for case in cases
        ]

    return asyncio.run(_all())


def merge_traces(records: list[dict], trace_path: Path, run_id: str) -> None:
    if not trace_path.exists():
        return
    by_qid: dict[str, dict] = {}
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        trace = json.loads(line)
        by_qid[trace["query_id"]] = trace
    for record in records:
        trace = by_qid.get(f"{run_id}-{record['case']}")
        if trace is None:
            record["trace_missing"] = True
            continue
        record["stages_ms"] = trace.get("stage_elapsed_ms") or {}
        record["generate_context_evidence_ids"] = list(
            trace.get("generate_context_evidence_ids") or []
        )
        usage = trace.get("model_usage") or {}
        record["token_usage"] = {
            k: usage.get(k)
            for k in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "estimated_cost",
                "pricing_status",
            )
        }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[idx], 2)


def build_summary(cases: list[dict], records: list[dict], judgments: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    for r in records:
        status_counts[r["status"] or "error"] = (
            status_counts.get(r["status"] or "error", 0) + 1
        )
    refuse_j = [j for j in judgments if j["expected"] == "refuse"]
    answer_j = [j for j in judgments if j["expected"] == "answer"]
    locate_j = [j for j in judgments if j["expected"] == "locate"]
    premise_j = [j for j in judgments if j["expected"] in PREMISE_EXPECTED]
    gen_lat = [
        r["stages_ms"]["generation"]
        for r in records
        if (r.get("stages_ms") or {}).get("generation") is not None
    ]
    total_lat = [
        r["stages_ms"]["total"]
        for r in records
        if (r.get("stages_ms") or {}).get("total") is not None
    ]
    tokens_in = sum(
        (r.get("token_usage") or {}).get("input_tokens") or 0 for r in records
    )
    tokens_out = sum(
        (r.get("token_usage") or {}).get("output_tokens") or 0 for r in records
    )
    claims = [c for r in records for c in ((r.get("answer") or {}).get("claims") or [])]
    source_removed = sorted({
        case["id"] for case in cases
        for ev in case.get("evidence") or []
        if "source_document_removed" in (ev.get("pending") or [])
    })
    return {
        "classification": "diagnostic (generation layer, real LLM, NOT formal)",
        "status_distribution": status_counts,
        "claim_tiers": {
            "verified": sum(1 for c in claims if c.get("verified", True)),
            "unverified_caveat": sum(1 for c in claims if c.get("verified") is False),
            "answers_with_unverified_claims": sum(
                1 for r in records
                if any(c.get("verified") is False
                       for c in ((r.get("answer") or {}).get("claims") or []))
            ),
        },
        "cases_with_removed_source_documents": source_removed,
        "refusal": {
            "denominator": len(refuse_j),
            "refuse_correct": sum(1 for j in refuse_j if j.get("refuse_correct")),
            "cases": [
                {"case": j["case"], "status": j["status"],
                 "refuse_correct": j.get("refuse_correct")}
                for j in refuse_j
            ],
        },
        "correct_premise": {
            "denominator": len(premise_j),
            "acceptable_status": sum(1 for j in premise_j if j.get("acceptable_status")),
            "needs_semantic_review": [
                j["case"] for j in premise_j if j.get("needs_semantic_review")
            ],
            "cases": [
                {"case": j["case"], "status": j["status"]} for j in premise_j
            ],
        },
        "answer_expected": {
            "denominator": len(answer_j),
            "answered": sum(1 for j in answer_j if j.get("answered")),
            "with_support_problems": sum(
                1 for j in answer_j if j.get("claim_support_problems")
            ),
            "cases": answer_j,
        },
        "locate_observed": {
            "denominator": len(locate_j),
            "statuses": {
                s: sum(1 for j in locate_j if j["status"] == s)
                for s in sorted({j["status"] for j in locate_j})
            },
            "answered_locate_cases": [
                j["case"] for j in locate_j if j["status"] == "answered"
            ],
        },
        "failure_classes": {
            fc: sum(1 for r in records if r.get("failure_class") == fc)
            for fc in sorted({r.get("failure_class") for r in records} - {None})
        },
        "latency_ms": {
            "generation": {
                "p50": _percentile(gen_lat, 0.5),
                "p95": _percentile(gen_lat, 0.95),
                "mean": (
                    round(sum(gen_lat) / len(gen_lat), 2) if gen_lat else 0.0
                ),
                "n": len(gen_lat),
            },
            "total": {
                "p50": _percentile(total_lat, 0.5),
                "p95": _percentile(total_lat, 0.95),
                "mean": (
                    round(sum(total_lat) / len(total_lat), 2) if total_lat else 0.0
                ),
                "n": len(total_lat),
            },
        },
        "token_usage_totals": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "pricing_status": "not_configured (cost rates not set; tokens only)",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", type=Path, default=DATASET,
                        help="golden JSONL (default: eval/golden_v7.jsonl)")
    parser.add_argument("--staging-corpus", default=None,
                        help="evaluate a staged (unpublished) corpus version: queries its "
                             "ingest-manifest collection instead of the live alias")
    args = parser.parse_args()
    dataset = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset

    started = time.perf_counter()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # ZAI_API_KEY etc. live in .env (not committed)
    cases = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    staging_collection = None
    if args.staging_corpus:
        manifest = json.loads((ROOT / "data" / "canonical" / "v2" / args.staging_corpus
                               / "ingest_manifest.json").read_text(encoding="utf-8"))
        staging_collection = manifest["collection_name"]
        os.environ["QDRANT_COLLECTION"] = staging_collection
        pointer = {"corpus_version": args.staging_corpus}
    problems = _binding_problems(cases, pointer)
    if problems:
        print(json.dumps({"binding_problems": problems}, ensure_ascii=False))
        raise SystemExit(2)

    settings = LlmProviderSettings.from_env()  # fail-closed: no key -> refuse
    real_generator = build_answer_generator(settings)
    recording = RecordingAnswerGenerator(real_generator)

    run_dir = ROOT / "eval" / "runs" / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run dir already exists: {run_dir}")
    (run_dir / "cases").mkdir(parents=True)

    # exact production composition, generator wrapped for prompt capture
    original_builder = server_runtime._build_answer_generator
    # Golden questions carry no conversation history, so no rewriter: the
    # run sends only the authorized retrieval context, nothing extra.
    server_runtime._build_answer_generator = lambda: (recording, True, None)
    try:
        services = server_runtime.build_query_services(
            project_root=ROOT,
            trace_output_path=run_dir / "query-traces.jsonl",
        )
    finally:
        server_runtime._build_answer_generator = original_builder

    corpus_version = args.staging_corpus or services.corpus_version
    run_config = {
        "classification": "diagnostic",
        "run_id": args.run_id,
        "dataset": str(dataset.relative_to(ROOT)),
        "dataset_sha256": _dataset_sha256(dataset),
        "dataset_cases": len(cases),
        "corpus_version": corpus_version,
        "staging_collection": staging_collection,
        "provider": settings.provider,
        "model": settings.model,
        "temperature": settings.temperature,
        "llm_base_url": settings.base_url,
        "retrieval": {
            "profile": "production answer profile (verified + caveated unverified evidence)",
            "retrieve_limit": 10,
            "generate_context_limit": 5,
            "context_expansion": services.answer._context_expander is not None,
        },
        "note": "real LLM run, authorized by the owner for the already-wired provider",
    }
    (run_dir / "config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    records = run_cases(services, cases, corpus_version, args.run_id)
    merge_traces(records, run_dir / "query-traces.jsonl", args.run_id)
    for record in records:
        record["refusal_stage"] = _refusal_stage(record)
    # attach the exact prompt inputs captured by the recording wrapper
    capture_by_qid = {c["request_id"]: c for c in recording.captures}
    for record in records:
        capture = capture_by_qid.get(f"{args.run_id}-{record['case']}")
        if capture is None:
            record["prompt_input"] = None  # generation never reached
        else:
            record["prompt_input"] = {
                "question_sent": capture["question_sent"],
                "conversation_context": capture["conversation_context"],
                "prompt_evidence": capture["prompt_evidence"],
            }
    judgments = [judge_case(case, record) for case, record in zip(cases, records)]
    summary = build_summary(cases, records, judgments)

    for record in records:
        (run_dir / "cases" / f"{record['case']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "judgments.jsonl").write_text(
        "\n".join(json.dumps(j, ensure_ascii=False) for j in judgments) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run dir: {run_dir}")
    print(f"elapsed: {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
