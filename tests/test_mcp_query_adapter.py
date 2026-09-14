import unittest
from dataclasses import asdict

from fastapi.testclient import TestClient
from mcp import Client

from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_source import EvidenceSourceService
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import Answer, Claim, EvidenceItem, QueryRequest
from src.mcp_adapter.server import create_mcp_server
from src.server.fastapi_app import create_app
from src.server.query_dto import AnswerResultDto


class _EvidenceCatalog:
    def __init__(self, *evidence: EvidenceItem):
        self._evidence = {item.evidence_id: item for item in evidence}

    def get(self, evidence_id: str):
        return self._evidence.get(evidence_id)


def _fixture():
    evidence = EvidenceItem(
        evidence_id="ev_mcp_rainfall",
        text="12小时降水量15.0~29.9毫米为大雨。",
        document_version_id="doc_rainfall",
        file_name="降水量等级.pdf",
        physical_pages=[4],
        heading_path=["表1"],
        usage_policy="answer_and_citation",
        source_uri="https://example.org/rainfall.pdf",
        jurisdiction="全国",
        document_kind="standard",
        effective_status="effective",
        source_authority="example",
    )
    answer_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=InMemoryEvidenceRetriever([evidence]),
        answer_generator=InMemoryAnswerGenerator(
            Answer(
                claims=[
                    Claim(
                        claim_id="claim-rainfall",
                        text="按12小时统计，20毫米属于大雨。",
                        evidence_ids=[evidence.evidence_id],
                    )
                ]
            )
        ),
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
    source_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=InMemoryEvidenceRetriever([locator]),
        answer_generator=InMemoryAnswerGenerator(None),
    )
    evidence_source = EvidenceSourceService(_EvidenceCatalog(evidence, locator))
    return evidence, locator, answer_service, source_service, evidence_source


class McpQueryAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_question_is_rejected_by_mcp_input_schema(self):
        _, _, answer_service, source_service, evidence_source = _fixture()
        server = create_mcp_server(
            query_service=answer_service,
            source_locator_service=source_service,
            evidence_catalog=evidence_source,
        )

        async with Client(server) as client:
            result = await client.call_tool(
                "answer_with_evidence",
                {"question": "", "conversation_context": {}},
            )

        self.assertTrue(result.is_error)

    async def test_server_exposes_only_the_three_approved_read_only_tools(self):
        _, _, answer_service, source_service, evidence_source = _fixture()
        server = create_mcp_server(
            query_service=answer_service,
            source_locator_service=source_service,
            evidence_catalog=evidence_source,
        )

        async with Client(server, raise_exceptions=True) as client:
            tools = (await client.list_tools()).tools

        self.assertEqual(
            {tool.name for tool in tools},
            {"answer_with_evidence", "search_evidence", "get_document_evidence"},
        )
        for tool in tools:
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertFalse(tool.annotations.open_world_hint)

    async def test_mcp_answer_matches_direct_and_http_semantics(self):
        evidence, _, answer_service, source_service, evidence_source = _fixture()
        request = {
            "request_id": "same-query-id",
            "question": "按12小时统计，降水量20毫米属于什么等级？",
            "conversation_context": {"statistical_period": "12h"},
        }
        direct = await answer_service.execute(
            QueryRequest(
                **request,
                corpus_version="formal-corpus-v1",
                caller_type="direct",
            )
        )
        expected = AnswerResultDto.model_validate(asdict(direct)).model_dump(
            mode="json"
        )
        http = TestClient(create_app(query_service=answer_service)).post(
            "/api/v1/queries", json=request
        )
        server = create_mcp_server(
            query_service=answer_service,
            source_locator_service=source_service,
            evidence_catalog=evidence_source,
        )

        async with Client(server, raise_exceptions=True) as client:
            mcp_result = await client.call_tool("answer_with_evidence", request)

        self.assertEqual(http.status_code, 200)
        self.assertEqual(http.json(), expected)
        self.assertEqual(mcp_result.structured_content, expected)
        self.assertEqual(mcp_result.content[0].type, "text")
        self.assertIn("20毫米属于大雨", mcp_result.content[0].text)
        self.assertEqual(mcp_result.content[1].type, "resource_link")
        self.assertEqual(
            str(mcp_result.content[1].uri), f"evidence://{evidence.evidence_id}"
        )

    async def test_search_tool_returns_locator_without_generation(self):
        _, locator, answer_service, source_service, evidence_source = _fixture()
        server = create_mcp_server(
            query_service=answer_service,
            source_locator_service=source_service,
            evidence_catalog=evidence_source,
        )

        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(
                "search_evidence",
                {
                    "request_id": "mcp-source-1",
                    "question": "用水量折算法的公式在哪里？",
                    "conversation_context": {},
                },
            )
            resource = await client.read_resource(f"evidence://{locator.evidence_id}")

        self.assertEqual(result.structured_content["status"], "search_only")
        self.assertIsNone(result.structured_content["answer"])
        self.assertEqual(
            result.structured_content["evidence"][0]["evidence_id"],
            locator.evidence_id,
        )
        self.assertEqual(
            str(result.content[1].uri), f"evidence://{locator.evidence_id}"
        )
        self.assertIn(locator.evidence_id, resource.contents[0].text)
        self.assertIn("source_locator_only", resource.contents[0].text)

    async def test_get_document_evidence_uses_stable_id_and_hides_missing_details(self):
        evidence, _, answer_service, source_service, evidence_source = _fixture()
        server = create_mcp_server(
            query_service=answer_service,
            source_locator_service=source_service,
            evidence_catalog=evidence_source,
        )

        async with Client(server) as client:
            found = await client.call_tool(
                "get_document_evidence", {"evidence_id": evidence.evidence_id}
            )
            resource = await client.read_resource(f"evidence://{evidence.evidence_id}")
            missing = await client.call_tool(
                "get_document_evidence", {"evidence_id": "ev_missing"}
            )

        self.assertFalse(found.is_error)
        self.assertEqual(found.structured_content["evidence_id"], evidence.evidence_id)
        self.assertEqual(
            str(found.content[1].uri), f"evidence://{evidence.evidence_id}"
        )
        self.assertIn(evidence.evidence_id, resource.contents[0].text)
        self.assertIn(evidence.source_uri, resource.contents[0].text)
        self.assertTrue(missing.is_error)
        self.assertEqual(missing.content[0].text, "evidence is not published")


if __name__ == "__main__":
    unittest.main()
