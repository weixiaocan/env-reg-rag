"""Deployment profiles keep the public demo separate from self-hosted use."""

import pytest

from src.server.deployment_profile import DeploymentSettings
from src.server.rate_limit import build_query_rate_limiter


def test_github_default_is_self_hosted() -> None:
    settings = DeploymentSettings.from_env({})

    assert settings.profile == "self_hosted"


def test_demo_refuses_to_start_without_rate_limiting() -> None:
    with pytest.raises(ValueError, match="demo profile requires"):
        build_query_rate_limiter(
            {
                "APP_PROFILE": "demo",
                "PUBLIC_RATE_LIMIT_ENABLED": "false",
            }
        )

