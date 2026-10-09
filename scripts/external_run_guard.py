"""Fail-closed authorization and cost guard for evaluation-only LLM runs."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass


def _normalized_url(value: str | None) -> str:
    return (value or "").strip().rstrip("/").lower()


@dataclass(frozen=True)
class ExternalRunAuthorization:
    provider: str
    model: str
    base_url: str
    send_scope: str
    note: str
    budget_cny: float
    per_call_reserve_cny: float

    def to_record(self) -> dict:
        return asdict(self)


def validate_external_run(
    settings,
    *,
    expected_scope: str,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    send_scope: str | None,
    note: str | None,
    budget_cny: float | None,
    per_call_reserve_cny: float | None,
) -> ExternalRunAuthorization:
    """Match explicit owner authorization to runtime settings and verified rates."""
    required = {
        "authorized_provider": provider,
        "authorized_model": model,
        "authorized_base_url": base_url,
        "authorized_send_scope": send_scope,
        "authorization_note": note,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError(f"missing explicit authorization fields: {', '.join(missing)}")
    mismatches: list[str] = []
    if provider.strip().lower() != settings.provider.strip().lower():
        mismatches.append("provider")
    if model.strip() != settings.model.strip():
        mismatches.append("model")
    if _normalized_url(base_url) != _normalized_url(settings.base_url):
        mismatches.append("base_url")
    if send_scope.strip() != expected_scope:
        mismatches.append("send_scope")
    if mismatches:
        raise ValueError(
            "authorization does not match runtime settings: " + ", ".join(mismatches)
        )
    if budget_cny is None or not math.isfinite(budget_cny) or budget_cny <= 0:
        raise ValueError("budget_cny must be positive")
    if (
        per_call_reserve_cny is None
        or not math.isfinite(per_call_reserve_cny)
        or per_call_reserve_cny <= 0
    ):
        raise ValueError("per_call_reserve_cny must be positive")
    if per_call_reserve_cny > budget_cny:
        raise ValueError("per_call_reserve_cny cannot exceed budget_cny")
    if settings.input_cost_per_million is None or settings.output_cost_per_million is None:
        raise ValueError("verified input and output token rates must be configured before a real run")
    if (
        not math.isfinite(settings.input_cost_per_million)
        or settings.input_cost_per_million <= 0
        or not math.isfinite(settings.output_cost_per_million)
        or settings.output_cost_per_million <= 0
    ):
        raise ValueError("configured input and output token rates must be finite and positive")
    if settings.cost_currency.upper() != "CNY":
        raise ValueError("budget is CNY but configured pricing currency is not CNY")
    return ExternalRunAuthorization(
        provider=settings.provider,
        model=settings.model,
        base_url=settings.base_url or "",
        send_scope=expected_scope,
        note=note.strip(),
        budget_cny=float(budget_cny),
        per_call_reserve_cny=float(per_call_reserve_cny),
    )


def can_start_call(spent_cny: float, authorization: ExternalRunAuthorization) -> bool:
    """Reserve a caller-authorized worst-case amount before starting a call."""
    return spent_cny + authorization.per_call_reserve_cny <= authorization.budget_cny


def add_verified_cost(spent_cny: float, usage: dict, authorization: ExternalRunAuthorization) -> float:
    cost = usage.get("estimated_cost")
    status = usage.get("pricing_status")
    if cost is None or status != "calculated":
        raise ValueError("model call did not return a calculated cost from configured rates")
    cost = float(cost)
    if not math.isfinite(cost) or cost < 0:
        raise ValueError("calculated model-call cost must be finite and non-negative")
    if cost > authorization.per_call_reserve_cny:
        raise ValueError("actual estimated call cost exceeded the authorized per-call reserve")
    total = spent_cny + cost
    if total > authorization.budget_cny:
        raise ValueError("actual estimated cumulative cost exceeded the authorized budget")
    return total
