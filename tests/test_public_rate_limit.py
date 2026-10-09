"""Deterministic public-query rate limit and HTTP contract tests."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.server.fastapi_app import create_app
from src.server.rate_limit import InMemoryQueryRateLimiter, QueryRateLimitPolicy


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _service() -> QueryApplicationService:
    return QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=InMemoryEvidenceRetriever([]),
        answer_generator=InMemoryAnswerGenerator(None),
    )


class PublicRateLimitTests(unittest.TestCase):
    def test_source_lookup_limit_resets_after_the_window(self) -> None:
        clock = _Clock()
        limiter = InMemoryQueryRateLimiter(
            QueryRateLimitPolicy(
                source_lookup_per_minute=2,
                answer_per_minute=1,
                answer_daily_budget=2,
            ),
            clock=clock,
            day_key=lambda: "2026-09-29",
        )

        self.assertTrue(limiter.check("visitor", "source_lookup").allowed)
        self.assertTrue(limiter.check("visitor", "source_lookup").allowed)
        denied = limiter.check("visitor", "source_lookup")
        self.assertFalse(denied.allowed)
        self.assertGreaterEqual(denied.retry_after_seconds, 1)
        clock.value = 61.0
        self.assertTrue(limiter.check("visitor", "source_lookup").allowed)

    def test_answer_daily_budget_is_global(self) -> None:
        limiter = InMemoryQueryRateLimiter(
            QueryRateLimitPolicy(
                source_lookup_per_minute=10,
                answer_per_minute=10,
                answer_daily_budget=2,
            ),
            clock=lambda: 0.0,
            day_key=lambda: "2026-09-29",
        )

        self.assertTrue(limiter.check("visitor-a", "answer").allowed)
        self.assertTrue(limiter.check("visitor-b", "answer").allowed)
        denied = limiter.check("visitor-c", "answer")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "answer_daily_budget_exhausted")

    def test_answer_daily_budget_is_also_limited_per_client(self) -> None:
        limiter = InMemoryQueryRateLimiter(
            QueryRateLimitPolicy(
                source_lookup_per_minute=10,
                answer_per_minute=10,
                answer_per_client_daily=2,
                answer_daily_budget=10,
            ),
            clock=lambda: 0.0,
            day_key=lambda: "2026-09-29",
        )

        self.assertTrue(limiter.check("visitor-a", "answer").allowed)
        self.assertTrue(limiter.check("visitor-a", "answer").allowed)
        denied = limiter.check("visitor-a", "answer")
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.reason, "answer_client_daily_limit_exceeded")
        self.assertTrue(limiter.check("visitor-b", "answer").allowed)

    def test_http_429_has_stable_error_and_retry_after(self) -> None:
        limiter = InMemoryQueryRateLimiter(
            QueryRateLimitPolicy(
                source_lookup_per_minute=10,
                answer_per_minute=1,
                answer_daily_budget=10,
            ),
            clock=lambda: 0.0,
            day_key=lambda: "2026-09-29",
        )
        client = TestClient(
            create_app(query_service=_service(), query_rate_limiter=limiter)
        )
        payload = {"question": "测试公开接口限流", "mode": "answer"}

        self.assertEqual(client.post("/api/v1/queries", json=payload).status_code, 200)
        response = client.post("/api/v1/queries", json=payload)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limit_exceeded")
        self.assertIn("retry-after", response.headers)


if __name__ == "__main__":
    unittest.main()
