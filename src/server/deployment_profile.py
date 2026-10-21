"""Validated runtime profiles for the public demo and self-hosted package."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping


DeploymentProfile = Literal["demo", "self_hosted"]


@dataclass(frozen=True)
class DeploymentSettings:
    profile: DeploymentProfile

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "DeploymentSettings":
        values = os.environ if env is None else env
        profile = values.get("APP_PROFILE", "self_hosted").strip().lower()
        if profile not in {"demo", "self_hosted"}:
            raise ValueError("APP_PROFILE must be demo or self_hosted")
        return cls(profile=profile)  # type: ignore[arg-type]
