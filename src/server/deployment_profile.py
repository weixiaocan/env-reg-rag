"""Validated runtime profiles for the public demo and self-hosted package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping


DeploymentProfile = Literal["demo", "self_hosted"]
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _enabled(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class DeploymentSettings:
    profile: DeploymentProfile
    diagnostic_routes_enabled: bool = False

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "DeploymentSettings":
        values = os.environ if env is None else env
        profile = values.get("APP_PROFILE", "self_hosted").strip().lower()
        if profile not in {"demo", "self_hosted"}:
            raise ValueError("APP_PROFILE must be demo or self_hosted")
        diagnostics = _enabled(
            values.get("PUBLIC_DIAGNOSTIC_ROUTES_ENABLED"), default=False
        )
        if profile == "demo" and diagnostics:
            raise ValueError("demo profile cannot expose diagnostic routes")
        return cls(
            profile=profile,  # type: ignore[arg-type]
            diagnostic_routes_enabled=diagnostics,
        )
