"""50-case END-TO-END diagnostic through the actual HTTP query entry.

Milestone-3 of the evaluation handoff: the SAME 50 golden cases driven
through ``POST /api/v1/queries`` (the production HTTP adapter, in-process
ASGI) with the EXACT production composition behind it -- scope resolution,
answer-profile retrieval, real LLM generation, citation validation, and the
production query trace.  Distinct from the generation-only diagnostic
(milestone 2): here the unit of measurement is the whole request->response
chain, including HTTP transport and serialization.

DIAGNOSTIC only.  Read-only w.r.t. the golden set, corpus, pointer, quality
statuses and --formal gates.

Usage (real run, authorized provider only):
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/run_e2e_diag.py \
        --run-id diagnostic-e2e-20261007
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from scripts.run_generation_diag import (  # noqa: E402
    DATASET,
    POINTER,
    RecordingAnswerGenerator,
    _binding_problems,
    _dataset_sha256,
)
from scripts.external_run_guard import (  # noqa: E402
    add_verified_cost,
    can_start_call,
    validate_external_run,
)
from src.server import runtime as server_runtime  # noqa: E402
from src.server.fastapi_app import create_app  # noqa: E402

SEND_SCOPE = "http-e2e:50-questions:production-retrieval-top5-chunks+same-clause-expansion-up-to-8"


def run_e2e_case(client: TestClient, case: dict, run_id: str) -> dict:
    request_id = f"{run_id}-{case['id']}"
    record: dict = {
        "case": case["id"],
        "question": case["question"],
        "expected_answerability": case["expected_answerability"],
        "request_id": request_id,
        # A record has no production trace until merge_traces proves that the
        # matching query_id was persisted.  Keeping this explicit prevents an
        # unmerged run from being reported as having zero missing traces.
        "trace_missing": True,
    }
    started = time.perf_counter()
    try:
        response = client.post(
            "/api/v1/queries",
            json={
                "request_id": request_id,
                "question": case["question"],
                "conversation_context": {},
                "mode": "answer",
            },
        )
        record["wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
        record["http_status"] = response.status_code
        if response.status_code == 200:
            body = response.json()
            record["status"] = body.get("status")
            record["answer"] = body.get("answer")
            record["evidence_ids"] = [
                e.get("evidence_id") for e in (body.get("evidence") or [])
            ]
            record["retrieved_evidence"] = list(body.get("evidence") or [])
            record["warnings"] = list(body.get("warnings") or [])
            record["missing_conditions"] = list(
                body.get("missing_conditions") or []
            )
        else:
            record["status"] = None
            record["failure_class"] = (
                "http_error"
                if response.status_code < 500
                else "http_server_error"
            )
            record["error_body"] = response.text[:500]
    except Exception as exc:  # noqa: BLE001 -- record and continue
        record["wall_ms"] = round((time.perf_counter() - started) * 1000, 3)
        record["failure_class"] = "transport_error"
        record["error_type"] = type(exc).__name__
        record["error_message"] = str(exc)[:500]
    return record


def run_e2e_cases(client: TestClient, cases: list[dict], run_id: str) -> list[dict]:
    """Injectable core (TestClient is duck-typed: any object with .post)."""
    return [run_e2e_case(client, case, run_id) for case in cases]


def merge_traces(records: list[dict], trace_path: Path) -> None:
    if not trace_path.exists():
        return
    by_qid: dict[str, dict] = {}
    parse_errors: list[str] = []
    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            trace = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"line {line_number}: {exc.msg}")
            continue
        by_qid[trace["query_id"]] = trace
    for record in records:
        if parse_errors:
            record["trace_parse_errors"] = parse_errors
        trace = by_qid.get(record["request_id"])
        if trace is None:
            record["trace_missing"] = True
            continue
        record["trace_missing"] = False
        record["stages_ms"] = trace.get("stage_elapsed_ms") or {}
        record["generate_context_evidence_ids"] = list(
            trace.get("generate_context_evidence_ids") or []
        )
        record["trace_evidence_ids"] = list(trace.get("evidence_ids") or [])
        usage = trace.get("model_usage") or {}
        record["token_usage"] = {
            k: usage.get(k)
            for k in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "estimated_cost",
                "currency",
                "pricing_status",
            )
        }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[idx], 2)


def classify_outcome(record: dict) -> str:
    expected = record["expected_answerability"]
    status = record.get("status")
    if record.get("failure_class"):
        return record["failure_class"]
    if expected == "refuse":
        return "correct_refusal" if status == "refused" else "refusal_failure"
    if expected == "correct_premise":
        if status == "refused":
            return "correct_refusal"
        if status in {"answered", "conflicting_sources"}:
            return "premise_answer_needs_semantic_review"
        return "premise_failure"
    if status in {"answered", "conflicting_sources"}:
        return "answered_needs_semantic_review"
    if status == "search_only":
        return "evidence_admission"
    if status == "needs_clarification":
        return "scope_clarification"
    if status == "refused":
        return (
            "generation_refusal"
            if record.get("generate_context_evidence_ids")
            else "retrieval_or_admission_refusal"
        )
    return "unknown"


def build_summary(records: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    for r in records:
        key = r.get("status") or r.get("failure_class") or "unknown"
        status_counts[key] = status_counts.get(key, 0) + 1
    wall = [r["wall_ms"] for r in records if r.get("wall_ms") is not None]
    retrieval = [
        r["stages_ms"]["retrieval"]
        for r in records
        if (r.get("stages_ms") or {}).get("retrieval") is not None
    ]
    generation = [
        r["stages_ms"]["generation"]
        for r in records
        if (r.get("stages_ms") or {}).get("generation") is not None
    ]
    tokens_in = sum(
        (r.get("token_usage") or {}).get("input_tokens") or 0 for r in records
    )
    tokens_out = sum(
        (r.get("token_usage") or {}).get("output_tokens") or 0 for r in records
    )
    total_tokens = sum(
        (r.get("token_usage") or {}).get("total_tokens") or 0 for r in records
    )
    costs = [
        float((r.get("token_usage") or {}).get("estimated_cost"))
        for r in records
        if (r.get("token_usage") or {}).get("estimated_cost") is not None
    ]
    pricing_statuses = sorted({
        (r.get("token_usage") or {}).get("pricing_status")
        for r in records
        if (r.get("token_usage") or {}).get("pricing_status")
    })
    return {
        "classification": (
            "diagnostic (end-to-end via HTTP entry, real LLM, NOT formal)"
        ),
        "status_distribution": status_counts,
        "denominators": {
            "all_cases": len(records),
            "content_target_cases": sum(
                1 for r in records if r["expected_answerability"] == "answer"
            ),
            "premise_cases": sum(
                1 for r in records
                if r["expected_answerability"] == "correct_premise"
            ),
            "refusal_cases": sum(
                1 for r in records if r["expected_answerability"] == "refuse"
            ),
        },
        "diagnostic_outcomes": {
            outcome: sum(
                1 for r in records if r.get("diagnostic_outcome") == outcome
            )
            for outcome in sorted(
                {r.get("diagnostic_outcome") for r in records} - {None}
            )
        },
        "trace_missing_count": sum(
            1 for r in records if r.get("trace_missing")
        ),
        "failure_classes": {
            fc: sum(1 for r in records if r.get("failure_class") == fc)
            for fc in sorted({r.get("failure_class") for r in records} - {None})
        },
        "latency_ms": {
            "wall": {
                "p50": _percentile(wall, 0.5),
                "p95": _percentile(wall, 0.95),
                "mean": round(sum(wall) / len(wall), 2) if wall else 0.0,
                "n": len(wall),
            },
            "retrieval_stage": {
                "p50": _percentile(retrieval, 0.5),
                "p95": _percentile(retrieval, 0.95),
                "n": len(retrieval),
            },
            "generation_stage": {
                "p50": _percentile(generation, 0.5),
                "p95": _percentile(generation, 0.95),
                "n": len(generation),
            },
        },
        "token_usage_totals": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": total_tokens,
            "estimated_cost": round(sum(costs), 8),
            "currencies": sorted({
                (r.get("token_usage") or {}).get("currency")
                for r in records
                if (r.get("token_usage") or {}).get("currency")
            }),
            "pricing_statuses": pricing_statuses,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authorization-note")
    parser.add_argument("--budget-cny", type=float)
    parser.add_argument("--per-call-reserve-cny", type=float)
    parser.add_argument("--authorized-provider")
    parser.add_argument("--authorized-model")
    parser.add_argument("--authorized-base-url")
    parser.add_argument("--authorized-send-scope")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    args = parser.parse_args()
    dataset = args.dataset if args.dataset.is_absolute() else ROOT / args.dataset

    cases = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    problems = _binding_problems(cases, pointer)
    if problems:
        print(json.dumps({"binding_problems": problems}, ensure_ascii=False))
        raise SystemExit(2)
    if args.preflight_only:
        preflight = {
            "classification": "offline preflight; no provider loaded; no model call",
            "dataset": str(dataset.relative_to(ROOT)).replace("\\", "/"),
            "dataset_sha256": _dataset_sha256(dataset),
            "dataset_cases": len(cases),
            "corpus_version": pointer["corpus_version"],
            "entry": "POST /api/v1/queries (in-process ASGI)",
            "send_scope_required_for_authorization": SEND_SCOPE,
        }
        preflight_path = ROOT / "eval" / "audits" / f"{args.run_id}-preflight.json"
        preflight_path.write_text(
            json.dumps(preflight, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        preflight["artifact"] = str(preflight_path.relative_to(ROOT)).replace("\\", "/")
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return

    started = time.perf_counter()
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # ZAI_API_KEY etc. live in .env (not committed)

    from src.adapters.llm_provider import (
        LlmProviderSettings,
        build_answer_generator,
    )

    settings = LlmProviderSettings.from_env()  # fail-closed: no key -> refuse
    try:
        authorization = validate_external_run(
            settings,
            expected_scope=SEND_SCOPE,
            provider=args.authorized_provider,
            model=args.authorized_model,
            base_url=args.authorized_base_url,
            send_scope=args.authorized_send_scope,
            note=args.authorization_note,
            budget_cny=args.budget_cny,
            per_call_reserve_cny=args.per_call_reserve_cny,
        )
    except ValueError as exc:
        raise SystemExit(f"external run blocked: {exc}") from exc
    recording = RecordingAnswerGenerator(build_answer_generator(settings))

    run_dir = ROOT / "eval" / "runs" / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run dir already exists: {run_dir}")
    (run_dir / "cases").mkdir(parents=True)

    # exact production composition; generator wrapped for prompt capture
    original_builder = server_runtime._build_answer_generator
    # Golden questions carry no conversation history, so no rewriter: the
    # run sends only the authorized retrieval context, nothing extra.
    server_runtime._build_answer_generator = lambda: (recording, True, None)
    try:
        services = server_runtime.build_query_services(
            project_root=ROOT,
            trace_output_path=run_dir / "query-traces.jsonl",
        )
        app = create_app(
            query_service=services.answer,
            source_locator_service=services.source_lookup,
            answer_corpus_version=services.corpus_version,
            source_lookup_corpus_version=services.corpus_version,
            # the deployment rate limiter is an ops guard, not part of the
            # query path under evaluation; disabled so 50 sequential requests
            # are not artificially rejected (recorded in run config)
            query_rate_limiter=None,
        )
    finally:
        server_runtime._build_answer_generator = original_builder

    run_config = {
        "classification": "diagnostic",
        "run_id": args.run_id,
        "dataset": str(dataset.relative_to(ROOT)).replace("\\", "/"),
        "dataset_sha256": _dataset_sha256(dataset),
        "dataset_cases": len(cases),
        "corpus_version": services.corpus_version,
        "entry": "POST /api/v1/queries (in-process ASGI, production HTTP adapter)",
        "provider": settings.provider,
        "model": settings.model,
        "temperature": settings.temperature,
        "rate_limiter": "disabled (ops guard, not under evaluation)",
        "pricing": {
            "input_cost_per_million": settings.input_cost_per_million,
            "output_cost_per_million": settings.output_cost_per_million,
            "currency": settings.cost_currency,
            "status": "configured and required by external-run guard",
        },
        "external_data_authorized": True,
        "authorization": authorization.to_record(),
        "budget_guard_limit": "per-call reserve is checked before dispatch; actual calculated cost is checked after response, so this is not a provider-side hard spending cap",
    }
    (run_dir / "config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Exercise the HTTP adapter as a real caller sees it.  TestClient's
    # default re-raises application exceptions, which would misclassify an
    # HTTP 500 as a transport failure and skip response/status recording.
    client = TestClient(app, raise_server_exceptions=False)
    records: list[dict] = []
    spent_cny = 0.0
    for case in cases:
        if can_start_call(spent_cny, authorization):
            record = run_e2e_case(client, case, args.run_id)
            merge_traces([record], run_dir / "query-traces.jsonl")
        else:
            record = {
                "case": case["id"],
                "question": case["question"],
                "expected_answerability": case["expected_answerability"],
                "request_id": f"{args.run_id}-{case['id']}",
                "trace_missing": True,
                "status": None,
                "failure_class": "budget_not_run",
                "wall_ms": None,
                "token_usage": {},
            }
        capture = next(
            (
                item for item in reversed(recording.captures)
                if item["request_id"] == record["request_id"]
            ),
            None,
        )
        record["prompt_input"] = (
            None
            if capture is None
            else {
                "question_sent": capture["question_sent"],
                "conversation_context": capture["conversation_context"],
                "prompt_evidence": capture["prompt_evidence"],
            }
        )
        record["diagnostic_outcome"] = classify_outcome(record)
        if capture is not None:
            try:
                spent_cny = add_verified_cost(
                    spent_cny, record.get("token_usage") or {}, authorization
                )
            except ValueError as exc:
                record["budget_guard_error"] = str(exc)
                record["diagnostic_outcome"] = "budget_guard_failure"
        record["cumulative_estimated_cost_cny"] = round(spent_cny, 8)
        records.append(record)
        (run_dir / "cases" / f"{record['case']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if record.get("budget_guard_error"):
            break
    summary = build_summary(records)
    summary["budget"] = {
        "authorized_cny": authorization.budget_cny,
        "per_call_reserve_cny": authorization.per_call_reserve_cny,
        "estimated_spent_cny": round(spent_cny, 8),
        "completed_case_records": len(records),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"run dir: {run_dir}")
    print(f"elapsed: {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
