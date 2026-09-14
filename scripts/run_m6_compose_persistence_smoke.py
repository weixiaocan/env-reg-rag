"""Restart the Compose services and prove Qdrant state survives."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m6-compose-persistence-v1.json"
APP_URL = "http://127.0.0.1:8000"
QDRANT_URL = "http://127.0.0.1:6333"
ALIASES = ("corpus_current", "m3_experiment_current")


def qdrant_state(client: httpx.Client) -> dict:
    aliases_response = client.get("/aliases")
    aliases_response.raise_for_status()
    aliases = {
        item["alias_name"]: item["collection_name"]
        for item in aliases_response.json()["result"]["aliases"]
        if item["alias_name"] in ALIASES
    }
    counts = {}
    for alias in ALIASES:
        response = client.get(f"/collections/{alias}")
        response.raise_for_status()
        counts[alias] = response.json()["result"]["points_count"]
    return {"aliases": aliases, "point_counts": counts}


def wait_until_ready(timeout_seconds: float = 120) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    with httpx.Client(base_url=APP_URL, timeout=5, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/v1/ready")
                if response.status_code == 200:
                    return response.json()
                last_error = response.text
            except Exception as exc:
                last_error = type(exc).__name__
            time.sleep(2)
    raise RuntimeError(f"app did not become ready: {last_error}")


def main() -> None:
    with httpx.Client(base_url=QDRANT_URL, timeout=10, trust_env=False) as client:
        before = qdrant_state(client)

    restarted_at = datetime.now(timezone.utc).isoformat()
    subprocess.run(
        ["docker", "compose", "restart", "qdrant", "app"],
        cwd=ROOT,
        check=True,
    )
    ready = wait_until_ready()

    with httpx.Client(base_url=QDRANT_URL, timeout=10, trust_env=False) as client:
        after = qdrant_state(client)
    with httpx.Client(base_url=APP_URL, timeout=10, trust_env=False) as client:
        clarification = client.post(
            "/api/v1/queries",
            json={"question": "降水量为20毫米时属于什么等级？"},
        )
        clarification.raise_for_status()
        clarification_body = clarification.json()

    checks = {
        "aliases_unchanged": before["aliases"] == after["aliases"],
        "point_counts_unchanged": before["point_counts"] == after["point_counts"],
        "formal_points_present": after["point_counts"]["corpus_current"] == 29,
        "experiment_points_present": (
            after["point_counts"]["m3_experiment_current"] == 58
        ),
        "app_ready": ready["status"] == "ready",
        "query_recovered": clarification_body["status"] == "needs_clarification",
    }
    report = {
        "evaluation_id": "m6-compose-persistence-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "restarted_at": restarted_at,
        "passed": all(checks.values()),
        "checks": checks,
        "before": before,
        "after": after,
        "readiness": ready,
        "query_status": clarification_body["status"],
        "model_called": False,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
