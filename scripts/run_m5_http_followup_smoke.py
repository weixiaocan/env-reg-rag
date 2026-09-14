"""Run a two-turn HTTP smoke against the local FastAPI workbench."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m5-http-followup-smoke-v1.json"
BASE_URL = "http://127.0.0.1:8000"
EXPECTED_EVIDENCE_ID = "ev_18de8c71615152558e1368e48ee73d1d"


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=90) as client:
        health = client.get("/api/v1/health")
        first = client.post(
            "/api/v1/queries",
            json={"question": "降水量为20毫米时属于什么等级？"},
        )
        started = time.perf_counter()
        second = client.post(
            "/api/v1/queries",
            json={
                "question": "12小时",
                "conversation_context": first.json()["continuation_context"],
            },
        )
        second_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    health.raise_for_status()
    first.raise_for_status()
    second.raise_for_status()
    first_result = first.json()
    second_result = second.json()
    cited_ids = {
        evidence_id
        for claim in (second_result.get("answer") or {}).get("claims", [])
        for evidence_id in claim["evidence_ids"]
    }
    target_evidence = next(
        (
            item
            for item in second_result["evidence"]
            if item["evidence_id"] == EXPECTED_EVIDENCE_ID
        ),
        None,
    )
    answer_text = "\n".join(
        claim["text"]
        for claim in (second_result.get("answer") or {}).get("claims", [])
    )
    passed = (
        health.json() == {"status": "ok"}
        and first_result["status"] == "needs_clarification"
        and first_result["missing_conditions"] == ["statistical_period"]
        and second_result["status"] == "answered"
        and second_result["resolved_scope"].get("statistical_period") == "12h"
        and "大雨" in answer_text
        and EXPECTED_EVIDENCE_ID in cited_ids
        and target_evidence is not None
        and target_evidence["physical_pages"] == [4]
    )
    report = {
        "evaluation_id": "m5-http-followup-smoke-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "second_turn_elapsed_ms": second_elapsed_ms,
        "passed": passed,
        "turns": [first_result, second_result],
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "first_status": first_result["status"],
                "second_status": second_result["status"],
                "second_turn_elapsed_ms": second_elapsed_ms,
                "target_page": target_evidence["physical_pages"]
                if target_evidence
                else None,
                "report": str(OUTPUT_PATH),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
