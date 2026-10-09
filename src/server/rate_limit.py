"""Small single-process safety net for public query endpoints.

Edge rate limiting and provider-side spend caps remain required for a public
deployment.  This limiter protects the single-worker POC when those layers are
misconfigured and enforces a global daily ceiling for paid answer attempts.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Literal, Mapping, Protocol


QueryMode = Literal["answer", "source_lookup"]


@dataclass(frozen=True)
class QueryRateLimitPolicy:
    source_lookup_per_minute: int = 30
    answer_per_minute: int = 3
    answer_per_client_daily: int = 10
    answer_daily_budget: int = 20

    def __post_init__(self) -> None:
        if min(
            self.source_lookup_per_minute,
            self.answer_per_minute,
            self.answer_per_client_daily,
            self.answer_daily_budget,
        ) < 1:
            raise ValueError("query rate limits must be positive")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str = "allowed"


class QueryRateLimiter(Protocol):
    def check(self, client_key: str, mode: QueryMode) -> RateLimitDecision: ...


class InMemoryQueryRateLimiter:
    """Thread-safe limiter for the repository's single-process web service."""

    _WINDOW_SECONDS = 60.0

    def __init__(
        self,
        policy: QueryRateLimitPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        day_key: Callable[[], str] | None = None,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._day_key = day_key or (lambda: datetime.now(UTC).date().isoformat())
        self._events: dict[tuple[str, QueryMode], deque[float]] = defaultdict(deque)
        self._answer_day = self._day_key()
        self._answer_count = 0
        self._answer_client_counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def check(self, client_key: str, mode: QueryMode) -> RateLimitDecision:
        now = self._clock()
        key = (client_key or "unknown", mode)
        per_minute = (
            self._policy.answer_per_minute
            if mode == "answer"
            else self._policy.source_lookup_per_minute
        )
        with self._lock:
            events = self._events[key]
            cutoff = now - self._WINDOW_SECONDS
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= per_minute:
                wait = max(1, int(self._WINDOW_SECONDS - (now - events[0]) + 0.999))
                return RateLimitDecision(False, wait, "per_minute_limit_exceeded")

            if mode == "answer":
                current_day = self._day_key()
                if current_day != self._answer_day:
                    self._answer_day = current_day
                    self._answer_count = 0
                    self._answer_client_counts.clear()
                if (
                    self._answer_client_counts[key[0]]
                    >= self._policy.answer_per_client_daily
                ):
                    return RateLimitDecision(
                        False,
                        3600,
                        "answer_client_daily_limit_exceeded",
                    )
                if self._answer_count >= self._policy.answer_daily_budget:
                    return RateLimitDecision(
                        False,
                        3600,
                        "answer_daily_budget_exhausted",
                    )
                self._answer_count += 1
                self._answer_client_counts[key[0]] += 1

            events.append(now)
            return RateLimitDecision(True)


def build_query_rate_limiter(
    env: Mapping[str, str] | None = None,
) -> QueryRateLimiter | None:
    values = os.environ if env is None else env
    profile = values.get("APP_PROFILE", "self_hosted").strip().lower()
    enabled = values.get(
        "PUBLIC_RATE_LIMIT_ENABLED", "true" if profile == "demo" else "false"
    ).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        if profile == "demo":
            raise ValueError("demo profile requires public rate limiting")
        return None
    return InMemoryQueryRateLimiter(
        QueryRateLimitPolicy(
            source_lookup_per_minute=int(
                values.get("PUBLIC_SOURCE_LOOKUP_PER_MINUTE", "30")
            ),
            answer_per_minute=int(values.get("PUBLIC_ANSWER_PER_MINUTE", "3")),
            answer_per_client_daily=int(
                values.get("PUBLIC_ANSWER_PER_CLIENT_DAILY", "10")
            ),
            answer_daily_budget=int(values.get("PUBLIC_ANSWER_DAILY_BUDGET", "20")),
        )
    )
