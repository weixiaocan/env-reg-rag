"""Dependency readiness contract for HTTP and container orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, str]


class ReadinessProbe(Protocol):
    def check(self) -> ReadinessReport: ...


class QdrantReadinessProbe:
    """Confirm Qdrant connectivity and the aliases required to serve queries."""

    def __init__(self, *, client, required_collections: Sequence[str]) -> None:
        self._client = client
        self._required_collections = tuple(required_collections)

    def check(self) -> ReadinessReport:
        try:
            self._client.get_collections()
            checks = {"qdrant": "ready"}
            for name in self._required_collections:
                checks[name] = (
                    "ready" if self._client.collection_exists(name) else "missing"
                )
        except Exception:
            return ReadinessReport(
                ready=False,
                checks={"qdrant": "unavailable"},
            )
        return ReadinessReport(
            ready=all(value == "ready" for value in checks.values()),
            checks=checks,
        )
