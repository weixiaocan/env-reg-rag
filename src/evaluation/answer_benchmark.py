"""Deterministic scoring and aggregation for repeated answer evaluations."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable


def evaluate_answer_case(case: dict, result: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if result["status"] != case["expected_status"]:
        failures.append(
            f"expected status {case['expected_status']}, got {result['status']}"
        )
    if case["expected_status"] == "needs_clarification":
        if result["missing_conditions"] != case.get("expected_missing_conditions", []):
            failures.append("missing conditions did not match")
    elif case["expected_status"] == "answered" and result.get("answer"):
        claims = result["answer"]["claims"]
        answer_text = "\n".join(claim["text"] for claim in claims)
        cited_ids = {
            evidence_id
            for claim in claims
            for evidence_id in claim["evidence_ids"]
        }
        accepted_ids = set(case.get("accepted_evidence_ids", []))
        if not accepted_ids.intersection(cited_ids):
            failures.append("answer did not cite an accepted evidence ID")
        missing_keywords = [
            keyword
            for keyword in case.get("expected_keywords", [])
            if keyword not in answer_text
        ]
        if missing_keywords:
            failures.append(f"answer missed keywords: {missing_keywords}")
    elif case["expected_status"] == "answered":
        failures.append("answered result had no answer")
    elif case["expected_status"] == "search_only":
        returned_ids = {item["evidence_id"] for item in result["evidence"]}
        if not set(case.get("accepted_evidence_ids", [])).intersection(returned_ids):
            failures.append("search-only result missed the expected locator")
    return not failures, failures


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def aggregate_runs(
    runs: list[dict[str, Any]],
    *,
    expected_rounds: int,
    expected_case_ids: list[str],
) -> dict[str, Any]:
    elapsed = [item["elapsed_ms"] for item in runs]
    stage_values: dict[str, list[float]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    model_usages = []
    for item in runs:
        by_case[item["case_id"]].append(item)
        trace = item.get("trace") or {}
        for stage, value in (trace.get("stage_elapsed_ms") or {}).items():
            stage_values[stage].append(value)
        usage = trace.get("model_usage") or {}
        if (usage.get("total_tokens") or 0) > 0:
            model_usages.append(usage)

    stable_cases = sum(
        len(by_case[case_id]) == expected_rounds
        and all(item["passed"] for item in by_case[case_id])
        for case_id in expected_case_ids
    )
    calculated_costs = [
        usage["estimated_cost"]
        for usage in model_usages
        if usage.get("estimated_cost") is not None
    ]
    def latency_summary(values: list[float]) -> dict[str, Any]:
        return {
            "sample_count": len(values),
            "p50": nearest_rank_percentile(values, 0.50),
            "p95": nearest_rank_percentile(values, 0.95),
            "max": max(values) if values else None,
        }

    return {
        "planned_requests": expected_rounds * len(expected_case_ids),
        "completed_requests": len(runs),
        "passed_requests": sum(item["passed"] for item in runs),
        "request_pass_rate": round(
            sum(item["passed"] for item in runs) / len(runs), 6
        )
        if runs
        else None,
        "error_count": sum(item.get("error_type") is not None for item in runs),
        "stable_cases": stable_cases,
        "stable_case_rate": round(stable_cases / len(expected_case_ids), 6)
        if expected_case_ids
        else None,
        "request_latency_ms": latency_summary(elapsed),
        "retrieval_latency_ms": latency_summary(stage_values["retrieval"]),
        "generation_latency_ms": latency_summary(stage_values["generation"]),
        "model_calls": len(model_usages),
        "input_tokens": sum(usage.get("input_tokens") or 0 for usage in model_usages),
        "output_tokens": sum(
            usage.get("output_tokens") or 0 for usage in model_usages
        ),
        "total_tokens": sum(usage["total_tokens"] for usage in model_usages),
        "estimated_cost": sum(calculated_costs)
        if len(calculated_costs) == len(model_usages) and model_usages
        else None,
        "cost_status": "calculated"
        if len(calculated_costs) == len(model_usages) and model_usages
        else "not_configured",
    }
