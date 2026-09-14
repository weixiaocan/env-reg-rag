"""Verify formula source lookup through the running FastAPI service."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m5-source-lookup-smoke-v1.json"
BASE_URL = "http://127.0.0.1:8000"
EXPECTED_EVIDENCE_ID = "ev_58337104c115f4ddb491ad736f859ae7"


def main() -> None:
    started = time.perf_counter()
    with httpx.Client(base_url=BASE_URL, timeout=90) as client:
        response = client.post(
            "/api/v1/queries",
            json={
                "question": "采用用水量折算法计算旱天入流入渗量时，具体公式是什么？",
                "mode": "source_lookup",
            },
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    response.raise_for_status()
    result = response.json()
    target = next(
        (
            item
            for item in result.get("evidence", [])
            if item["evidence_id"] == EXPECTED_EVIDENCE_ID
        ),
        None,
    )
    passed = (
        result["status"] == "search_only"
        and result["answer"] is None
        and result["corpus_version"] == "m3-experiment-v1"
        and target is not None
        and target["usage_policy"] == "source_locator_only"
        and target["physical_pages"] == [19]
        and target["heading_path"] == ["5.2.4"]
    )
    report = {
        "evaluation_id": "m5-source-lookup-smoke-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "elapsed_ms": elapsed_ms,
        "passed": passed,
        "result": result,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "status": result["status"],
                "answer": result["answer"],
                "elapsed_ms": elapsed_ms,
                "target_page": target["physical_pages"] if target else None,
                "target_clause": target["heading_path"] if target else None,
                "usage_policy": target["usage_policy"] if target else None,
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
