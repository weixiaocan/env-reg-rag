from types import SimpleNamespace

import pytest

from scripts.external_run_guard import add_verified_cost, can_start_call, validate_external_run


def _settings(**overrides):
    values = dict(
        provider="zhipu", model="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        input_cost_per_million=1.0, output_cost_per_million=2.0,
        cost_currency="CNY",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _validate(settings=None, **overrides):
    values = dict(
        expected_scope="scope-A", provider="zhipu", model="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        send_scope="scope-A", note="owner approved this exact run",
        budget_cny=10.0, per_call_reserve_cny=1.0,
    )
    values.update(overrides)
    return validate_external_run(settings or _settings(), **values)


def test_authorization_must_match_runtime_provider_model_url_and_scope():
    for field, value in [
        ("provider", "openai"), ("model", "other"),
        ("base_url", "https://example.invalid"), ("send_scope", "scope-B"),
    ]:
        with pytest.raises(ValueError, match="authorization does not match"):
            _validate(**{field: value})


def test_verified_cny_rates_are_required():
    with pytest.raises(ValueError, match="verified input and output token rates"):
        _validate(_settings(input_cost_per_million=None, output_cost_per_million=None))


@pytest.mark.parametrize("bad", [-1.0, 0.0, float("nan"), float("inf")])
def test_rates_budget_and_reserve_must_be_finite_and_positive(bad):
    with pytest.raises(ValueError):
        _validate(_settings(input_cost_per_million=bad))
    with pytest.raises(ValueError):
        _validate(budget_cny=bad)
    with pytest.raises(ValueError):
        _validate(per_call_reserve_cny=bad)


def test_budget_reserves_before_call_and_accounts_verified_cost():
    auth = _validate(budget_cny=2.0, per_call_reserve_cny=1.0)
    assert can_start_call(0.9, auth)
    assert not can_start_call(1.1, auth)
    assert add_verified_cost(0.9, {"estimated_cost": 0.5, "pricing_status": "calculated"}, auth) == 1.4
    with pytest.raises(ValueError, match="per-call reserve"):
        add_verified_cost(0.0, {"estimated_cost": 1.1, "pricing_status": "calculated"}, auth)
    for bad in [-1.0, float("nan"), float("inf")]:
        with pytest.raises(ValueError, match="finite and non-negative"):
            add_verified_cost(0.0, {"estimated_cost": bad, "pricing_status": "calculated"}, auth)
