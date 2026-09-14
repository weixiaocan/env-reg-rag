"""Run one real answer query and verify its local observability trace."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[1]
TRACE_PATH = ROOT / "data" / "observability" / "query-traces.jsonl"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m6-query-trace-smoke-v1.json"
BASE_URL = "http://127.0.0.1:8000"


def load_trace(query_id: str) -> dict:
    matches = []
    for line in TRACE_PATH.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("query_id") == query_id:
            matches.append(record)
    if len(matches) != 1:
        raise AssertionError(f"expected one trace for {query_id}, found {len(matches)}")
    return matches[0]


def main() -> None:
    query_id = f"m6-trace-{uuid4()}"
    with httpx.Client(base_url=BASE_URL, timeout=90, trust_env=False) as client:
        response = client.post(
            "/api/v1/queries",
            json={
                "request_id": query_id,
                "question": "12小时降水量为20毫米时属于什么等级？",
            },
        )
    response.raise_for_status()
    result = response.json()
    trace = load_trace(query_id)

    required_stages = {"scope", "retrieval", "generation", "validation", "total"}
    usage = trace["model_usage"]
    serialized_trace = json.dumps(trace, ensure_ascii=False).lower()
    checks = {
        "answered": result["status"] == "answered",
        "trace_status_matches": trace["status"] == result["status"],
        "all_stages_recorded": required_stages == set(trace["stage_elapsed_ms"]),
        "tokens_reported": (usage.get("total_tokens") or 0) > 0,
        "runtime_versioned": all(
            trace["runtime_metadata"].get(key)
            for key in ("model", "retrieval_profile", "collection_alias", "prompt_version")
        ),
        "no_api_key": "api_key" not in serialized_trace,
    }
    report = {
        "evaluation_id": "m6-query-trace-smoke-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "checks": checks,
        "query_result": {
            "query_id": query_id,
            "status": result["status"],
            "evidence_ids": [item["evidence_id"] for item in result["evidence"]],
        },
        "trace": trace,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "status": result["status"],
                "stage_elapsed_ms": trace["stage_elapsed_ms"],
                "model_usage": usage,
                "report": str(OUTPUT_PATH),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
