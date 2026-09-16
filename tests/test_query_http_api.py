import unittest

from fastapi.testclient import TestClient

from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import Answer, Claim, EvidenceItem
from src.server.fastapi_app import create_app
from src.server.readiness import ReadinessReport


class _FailingEvidenceRetriever:
    async def retrieve(self, **_kwargs):
        raise RuntimeError("provider-secret-detail")


class QueryHttpApiTest(unittest.TestCase):
    def test_unexpected_failure_does_not_leak_internal_details(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=_FailingEvidenceRetriever(),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(
            create_app(query_service=query_service),
            raise_server_exceptions=False,
        )

        response = client.post(
            "/api/v1/queries",
            json={"question": "武汉市混错接改造项目有什么要求？"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "internal_error",
                    "message": "an unexpected error occurred",
                }
            },
        )
        self.assertNotIn("provider-secret-detail", response.text)

    def test_invalid_query_uses_the_stable_error_contract(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(create_app(query_service=query_service))

        response = client.post("/api/v1/queries", json={"question": ""})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "invalid_request",
                    "message": "request validation failed",
                }
            },
        )

    def test_unconfigured_source_lookup_has_a_stable_error_code(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(create_app(query_service=query_service))

        response = client.post(
            "/api/v1/queries",
            json={"question": "公式在哪里？", "mode": "source_lookup"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "source_lookup_unavailable",
                    "message": "source lookup is not configured",
                }
            },
        )

    def test_user_can_find_an_unverified_formula_without_generating_an_answer(self):
        answer_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        locator = EvidenceItem(
            evidence_id="ev_formula_locator",
            text="5.2.4 用水量折算法公式位于本页，自动转写未经批准。",
            document_version_id="doc_formula",
            file_name="入流入渗监测与评估标准.pdf",
            physical_pages=[19],
            heading_path=["5.2.4"],
            usage_policy="source_locator_only",
            source_uri="https://example.org/formula.pdf",
        )
        locator_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([locator]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(
            create_app(
                query_service=answer_service,
                source_locator_service=locator_service,
            )
        )

        response = client.post(
            "/api/v1/queries",
            json={
                "question": "采用用水量折算法时，原文公式在哪里？",
                "mode": "source_lookup",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "search_only")
        self.assertIsNone(body["answer"])
        self.assertEqual(body["evidence"][0]["evidence_id"], "ev_formula_locator")
        self.assertEqual(body["evidence"][0]["physical_pages"], [19])
        self.assertEqual(body["evidence"][0]["heading_path"], ["5.2.4"])

    def test_user_can_open_the_evidence_workbench(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(create_app(query_service=query_service))

        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("排水法规标准智能问答系统", response.text)
        self.assertIn("当前发布语料", response.text)
        self.assertNotIn("正式语料 formal-corpus-v1", response.text)
        self.assertIn('aria-label="提交问题"', response.text)

    def test_health_check_is_available_without_running_a_query(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(create_app(query_service=query_service))

        response = client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_reports_required_dependencies(self):
        class ReadyProbe:
            def check(self):
                return ReadinessReport(
                    ready=True,
                    checks={
                        "qdrant": "ready",
                        "corpus_current": "ready",
                        "m3_experiment_current": "ready",
                    },
                )

        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(
            create_app(query_service=query_service, readiness_probe=ReadyProbe())
        )

        response = client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["checks"]["corpus_current"], "ready")

    def test_readiness_failure_returns_503_without_internal_details(self):
        class NotReadyProbe:
            def check(self):
                return ReadinessReport(
                    ready=False,
                    checks={"qdrant": "unavailable"},
                )

        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(
            create_app(query_service=query_service, readiness_probe=NotReadyProbe())
        )

        response = client.get("/api/v1/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "not_ready", "checks": {"qdrant": "unavailable"}},
        )

    def test_user_receives_a_structured_clarification_result(self):
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([]),
            answer_generator=InMemoryAnswerGenerator(None),
        )
        client = TestClient(create_app(query_service=query_service))

        response = client.post(
            "/api/v1/queries",
            json={
                "request_id": "api-rainfall-001",
                "question": "降水量为20毫米时属于什么等级？",
                "conversation_context": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["query_id"], "api-rainfall-001")
        self.assertEqual(body["status"], "needs_clarification")
        self.assertEqual(body["missing_conditions"], ["statistical_period"])
        self.assertIsNone(body["answer"])
        self.assertEqual(body["evidence"], [])
        self.assertEqual(
            body["continuation_context"],
            {"pending_question": "降水量为20毫米时属于什么等级？"},
        )
        self.assertEqual(body["corpus_version"], "formal-corpus-v1")

    def test_user_can_answer_a_clarifying_question_without_managing_query_ids(self):
        table = EvidenceItem(
            evidence_id="ev_rainfall_table",
            text="12小时降水量15.0~29.9毫米为大雨。",
            document_version_id="doc_rainfall",
            file_name="降水量等级.pdf",
            physical_pages=[4],
            heading_path=["表1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/rainfall.pdf",
        )
        query_service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever([table]),
            answer_generator=InMemoryAnswerGenerator(
                Answer(
                    claims=[
                        Claim(
                            claim_id="claim-rainfall",
                            text="按12小时统计，20毫米属于大雨。",
                            evidence_ids=[table.evidence_id],
                        )
                    ]
                )
            ),
        )
        client = TestClient(create_app(query_service=query_service))

        first = client.post(
            "/api/v1/queries",
            json={"question": "降水量为20毫米时属于什么等级？"},
        )
        second = client.post(
            "/api/v1/queries",
            json={
                "question": "12小时",
                "conversation_context": first.json()["continuation_context"],
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["query_id"])
        self.assertTrue(second.json()["query_id"])
        self.assertNotEqual(first.json()["query_id"], second.json()["query_id"])
        self.assertEqual(second.json()["status"], "answered")
        self.assertEqual(
            second.json()["answer"]["claims"][0]["evidence_ids"],
            ["ev_rainfall_table"],
        )
        self.assertEqual(second.json()["evidence"][0]["physical_pages"], [4])


if __name__ == "__main__":
    unittest.main()
