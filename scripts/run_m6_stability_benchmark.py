"""Run three resumable HTTP rounds over the approved M4 answer set."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.answer_benchmark import aggregate_runs, evaluate_answer_case


ANSWER_SET_PATH = ROOT / "data" / "evaluation" / "m4-answer-set-v1.json"
TRACE_PATH = ROOT / "data" / "observability" / "query-traces.jsonl"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "eval_results" / "m6-stability-benchmark-v1.json"
BASE_URL = "http://127.0.0.1:8000"


def load_latest_trace(query_id: str) -> dict[str, Any] | None:
    if not TRACE_PATH.exists():
        return None
    match = None
    for line in TRACE_PATH.read_text(encoding="utf-8").splitlines():
        trace = json.loads(line)
        if trace.get("query_id") == query_id:
            match = trace
    if match is not None:
        match.pop("question", None)
    return match


def compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    answer = result.get("answer") or {}
    return {
        "status": result.get("status"),
        "missing_conditions": result.get("missing_conditions", []),
        "claims": answer.get("claims", []),
        "evidence_ids": [item["evidence_id"] for item in result.get("evidence", [])],
        "warnings": result.get("warnings", []),
    }


def write_report(
    *,
    output_path: Path,
    answer_set: dict[str, Any],
    rounds: int,
    runs: list[dict[str, Any]],
) -> None:
    case_ids = [case["case_id"] for case in answer_set["cases"]]
    report = {
        "evaluation_id": "m6-stability-benchmark-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "answer_set_id": answer_set["answer_set_id"],
        "rounds": rounds,
        "summary": aggregate_runs(
            runs,
            expected_rounds=rounds,
            expected_case_ids=case_ids,
        ),
        "runs": runs,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(*, rounds: int, output_path: Path) -> None:
    answer_set = json.loads(ANSWER_SET_PATH.read_text(encoding="utf-8"))
    runs: list[dict[str, Any]] = []
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if existing.get("answer_set_id") != answer_set["answer_set_id"]:
            raise SystemExit("existing report uses a different answer set")
        runs = existing.get("runs", [])

    completed = {(item["round"], item["case_id"]) for item in runs}
    total = rounds * len(answer_set["cases"])
    with httpx.Client(base_url=BASE_URL, timeout=90, trust_env=False) as client:
        client.get("/api/v1/health").raise_for_status()
        for round_number in range(1, rounds + 1):
            for case in answer_set["cases"]:
                key = (round_number, case["case_id"])
                if key in completed:
                    continue
                query_id = f"m6-stability-r{round_number}-{case['case_id'].lower()}"
                position = len(runs) + 1
                print(
                    f"[{position}/{total}] round={round_number} "
                    f"case={case['case_id']} starting",
                    flush=True,
                )
                started = time.perf_counter()
                result = None
                error_type = None
                failures: list[str] = []
                try:
                    response = client.post(
                        "/api/v1/queries",
                        json={
                            "request_id": query_id,
                            "question": case["question"],
                            "conversation_context": case.get("context", {}),
                            "mode": "source_lookup"
                            if case["retrieval_profile"] == "locator"
                            else "answer",
                        },
                    )
                    response.raise_for_status()
                    result = response.json()
                    passed, failures = evaluate_answer_case(case, result)
                except Exception as exc:
                    passed = False
                    error_type = type(exc).__name__
                    failures = [f"{error_type}: {exc}"]
                elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                trace = load_latest_trace(query_id)
                if trace is None:
                    failures.append("query trace was not persisted")
                    passed = False
                run = {
                    "round": round_number,
                    "case_id": case["case_id"],
                    "expected_status": case["expected_status"],
                    "passed": passed,
                    "failures": failures,
                    "error_type": error_type,
                    "elapsed_ms": elapsed_ms,
                    "result": compact_result(result),
                    "trace": trace,
                }
                runs.append(run)
                write_report(
                    output_path=output_path,
                    answer_set=answer_set,
                    rounds=rounds,
                    runs=runs,
                )
                status = result.get("status") if result else error_type
                tokens = ((trace or {}).get("model_usage") or {}).get("total_tokens")
                print(
                    f"[{len(runs)}/{total}] case={case['case_id']} status={status} "
                    f"passed={passed} elapsed_ms={elapsed_ms} tokens={tokens}",
                    flush=True,
                )

    summary = aggregate_runs(
        runs,
        expected_rounds=rounds,
        expected_case_ids=[case["case_id"] for case in answer_set["cases"]],
    )
    write_report(
        output_path=output_path,
        answer_set=answer_set,
        rounds=rounds,
        runs=runs,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    if summary["completed_requests"] != summary["planned_requests"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    main(rounds=args.rounds, output_path=args.output)
