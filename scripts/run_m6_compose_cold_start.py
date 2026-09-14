"""Prove a fresh isolated Compose project can build Qdrant projections."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m6-compose-cold-start-v1.json"
PROJECT_NAME = "p15_rag_cold_test"
TEMP_VOLUME = "p15-rag-cold-test-qdrant-storage"
APP_URL = "http://127.0.0.1:18000"
QDRANT_URL = "http://127.0.0.1:16333"


def compose_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "QDRANT_CONTAINER_NAME": "p15-rag-cold-test-qdrant",
            "QDRANT_HTTP_HOST_PORT": "16333",
            "QDRANT_GRPC_HOST_PORT": "16334",
            "APP_HOST_PORT": "18000",
            "QDRANT_VOLUME_NAME": TEMP_VOLUME,
        }
    )
    return env


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT_NAME, *args],
        cwd=ROOT,
        env=compose_environment(),
        check=check,
        text=True,
        capture_output=True,
    )


def wait_until_ready(timeout_seconds: float = 180) -> dict:
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
    raise RuntimeError(f"cold-start app did not become ready: {last_error}")


def qdrant_counts() -> dict[str, int]:
    with httpx.Client(base_url=QDRANT_URL, timeout=10, trust_env=False) as client:
        counts = {}
        for alias in ("corpus_current", "m3_experiment_current"):
            response = client.get(f"/collections/{alias}")
            response.raise_for_status()
            counts[alias] = response.json()["result"]["points_count"]
        return counts


def main() -> None:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    cleanup_ok = False
    report = None
    try:
        compose("up", "-d", "--no-build")
        ready = wait_until_ready()
        counts = qdrant_counts()
        init_logs = compose("logs", "--no-color", "qdrant-init").stdout
        checks = {
            "app_ready": ready["status"] == "ready",
            "formal_projection_built": counts["corpus_current"] == 29,
            "experiment_projection_built": counts["m3_experiment_current"] == 58,
            "initializer_built_fresh_indexes": (
                '"formal": "built_and_published"' in init_logs
                and '"experiment": "built_and_published"' in init_logs
            ),
        }
        report = {
            "evaluation_id": "m6-compose-cold-start-v1",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "started_at": started_at,
            "cold_start_elapsed_seconds": round(time.perf_counter() - started, 3),
            "passed": all(checks.values()),
            "checks": checks,
            "readiness": ready,
            "point_counts": counts,
            "isolated_project": PROJECT_NAME,
            "temporary_volume": TEMP_VOLUME,
        }
    finally:
        compose("down", "--volumes", "--remove-orphans", check=False)
        volume_check = subprocess.run(
            ["docker", "volume", "inspect", TEMP_VOLUME],
            text=True,
            capture_output=True,
        )
        cleanup_ok = volume_check.returncode != 0

    if report is None:
        raise RuntimeError("cold-start verification did not produce a report")
    report["temporary_resources_removed"] = cleanup_ok
    report["passed"] = report["passed"] and cleanup_ok
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
