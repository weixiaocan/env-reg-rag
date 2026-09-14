"""Verify stable error envelopes through the running FastAPI service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m5-error-contract-v1.json"
BASE_URL = "http://127.0.0.1:8000"


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30, trust_env=False) as client:
        responses = {
            "invalid_query": client.post("/api/v1/queries", json={"question": ""}),
            "unknown_evidence": client.get("/api/v1/evidence/ev_missing"),
            "unknown_document": client.get("/api/v1/documents/doc_missing"),
        }

    observed = {
        name: {"status_code": response.status_code, "body": response.json()}
        for name, response in responses.items()
    }
    expected = {
        "invalid_query": (422, "invalid_request"),
        "unknown_evidence": (404, "evidence_not_found"),
        "unknown_document": (404, "document_not_found"),
    }
    passed = all(
        observed[name]["status_code"] == status
        and observed[name]["body"].get("error", {}).get("code") == code
        and set(observed[name]["body"].get("error", {})) == {"code", "message"}
        for name, (status, code) in expected.items()
    )
    report = {
        "evaluation_id": "m5-error-contract-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "passed": passed,
        "observed": observed,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
