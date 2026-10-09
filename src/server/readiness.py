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
    """Confirm the runtime query entry matches the published Qdrant state."""

    def __init__(
        self,
        *,
        client,
        required_collections: Sequence[str] = (),
        query_entry: str | None = None,
        expected_alias: str | None = None,
        expected_collection: str | None = None,
        expected_point_count: int | None = None,
    ) -> None:
        self._client = client
        self._required_collections = tuple(required_collections)
        self._query_entry = query_entry
        self._expected_alias = expected_alias
        self._expected_collection = expected_collection
        self._expected_point_count = expected_point_count

    def check(self) -> ReadinessReport:
        try:
            self._client.get_collections()
            checks = {"qdrant": "ready"}
            for name in self._required_collections:
                checks[name] = (
                    "ready" if self._client.collection_exists(name) else "missing"
                )
            if self._expected_alias is not None:
                checks["query_entry"] = (
                    "ready"
                    if self._query_entry == self._expected_alias
                    else "mismatch"
                )
            if self._expected_collection is not None:
                target_exists = self._client.collection_exists(
                    self._expected_collection
                )
                checks["pointer_target"] = (
                    "ready" if target_exists else "missing"
                )
                aliases = self._client.get_aliases().aliases
                alias_targets = {
                    getattr(item, "alias_name", None): getattr(
                        item, "collection_name", None
                    )
                    for item in aliases
                }
                checks["alias_target"] = (
                    "ready"
                    if alias_targets.get(self._expected_alias)
                    == self._expected_collection
                    else "mismatch"
                )
                if target_exists and self._expected_point_count is not None:
                    info = self._client.get_collection(self._expected_collection)
                    actual_points = int(getattr(info, "points_count", 0) or 0)
                    checks["point_count"] = (
                        "ready"
                        if actual_points == self._expected_point_count
                        else "mismatch"
                    )
                elif self._expected_point_count is not None:
                    checks["point_count"] = "missing"
        except Exception:
            return ReadinessReport(
                ready=False,
                checks={"qdrant": "unavailable"},
            )
        return ReadinessReport(
            ready=all(value == "ready" for value in checks.values()),
            checks=checks,
        )
