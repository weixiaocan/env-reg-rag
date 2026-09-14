"""MCP protocol mapping over the existing evidence-query application core."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Annotated
from uuid import uuid4

from mcp.server import MCPServer
from mcp.types import CallToolResult, ResourceLink, TextContent, ToolAnnotations
from pydantic import Field

from src.application.query_service import QueryApplicationService
from src.application.evidence_source import EvidenceSourceService
from src.domain.query import QueryRequest, QueryStatus
from src.server.query_dto import AnswerResultDto, EvidenceDto


READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
NonEmptyText = Annotated[str, Field(min_length=1)]


def _resource_link(evidence: EvidenceDto) -> ResourceLink:
    pages = ",".join(str(page) for page in evidence.physical_pages) or "未知"
    heading = " / ".join(evidence.heading_path) or "位置未知"
    return ResourceLink(
        name=evidence.evidence_id,
        title=f"{evidence.file_name} · 第{pages}页 · {heading}",
        uri=f"evidence://{evidence.evidence_id}",
        description=evidence.text,
        mimeType="application/json",
    )


def _answer_summary(result: AnswerResultDto) -> str:
    if result.answer and result.answer.claims:
        return "\n".join(claim.text for claim in result.answer.claims)
    if result.status == QueryStatus.NEEDS_CLARIFICATION:
        return "需要补充条件：" + "、".join(result.missing_conditions)
    if result.status == QueryStatus.SEARCH_ONLY:
        return f"已定位到 {len(result.evidence)} 条原文证据，请打开来源核验。"
    if result.status == QueryStatus.REFUSED:
        return "当前语料没有足够证据支持回答。"
    return f"查询状态：{result.status.value}"


def _result_content(result: AnswerResultDto) -> list:
    content = [TextContent(text=_answer_summary(result))]
    for evidence in result.evidence:
        content.append(_resource_link(evidence))
    return content


def _answer_call_result(result: AnswerResultDto) -> CallToolResult:
    return CallToolResult(
        content=_result_content(result),
        structuredContent=result.model_dump(mode="json"),
    )


def _evidence_call_result(evidence: EvidenceDto) -> CallToolResult:
    content = [TextContent(text=evidence.text), _resource_link(evidence)]
    return CallToolResult(
        content=content,
        structuredContent=evidence.model_dump(mode="json"),
    )


def create_mcp_server(
    *,
    query_service: QueryApplicationService,
    source_locator_service: QueryApplicationService,
    evidence_catalog: EvidenceSourceService,
) -> MCPServer:
    """Create a stateless MCP server that delegates all policy to application services."""
    server = MCPServer(
        name="drainage-regulation-rag",
        title="排水规范证据助手",
        description="只读检索环保法律法规、政策、标准和手册，并返回可核验依据。",
        version="0.1.0",
    )

    @server.resource(
        "evidence://{evidence_id}",
        name="evidence-by-id",
        title="已发布证据",
        description="按稳定 evidence ID 读取证据、页码、条款和公开来源。",
        mime_type="application/json",
    )
    def evidence_resource(evidence_id: str) -> str:
        evidence = evidence_catalog.get(evidence_id)
        if evidence is None:
            raise ValueError("evidence is not published")
        dto = EvidenceDto.model_validate(asdict(evidence))
        return json.dumps(dto.model_dump(mode="json"), ensure_ascii=False)

    @server.tool(
        name="answer_with_evidence",
        description=(
            "使用已批准的正式语料回答排水规范问题，并返回主张、证据、文档、"
            "条款和页码。条件不足时会要求澄清，不会猜测。"
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def answer_with_evidence(
        question: NonEmptyText,
        conversation_context: dict[str, str] | None = None,
        request_id: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, AnswerResultDto]:
        result = await query_service.execute(
            QueryRequest(
                request_id=request_id or str(uuid4()),
                question=question,
                conversation_context=dict(conversation_context or {}),
                corpus_version="formal-corpus-v1",
                caller_type="mcp",
            )
        )
        return _answer_call_result(AnswerResultDto.model_validate(asdict(result)))

    @server.tool(
        name="search_evidence",
        description=(
            "在扩展实验语料中定位相关原文、文档和页码。适合查找公式等尚未批准"
            "自动转写的内容；只返回搜索结果，不生成结论。"
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def search_evidence(
        question: NonEmptyText,
        conversation_context: dict[str, str] | None = None,
        request_id: NonEmptyText | None = None,
    ) -> Annotated[CallToolResult, AnswerResultDto]:
        result = await source_locator_service.execute(
            QueryRequest(
                request_id=request_id or str(uuid4()),
                question=question,
                conversation_context=dict(conversation_context or {}),
                corpus_version="m3-experiment-v1",
                caller_type="mcp",
            )
        )
        return _answer_call_result(AnswerResultDto.model_validate(asdict(result)))

    @server.tool(
        name="get_document_evidence",
        description="按稳定 evidence ID 回取单条已发布证据及其原文来源。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def get_document_evidence(
        evidence_id: NonEmptyText,
    ) -> Annotated[CallToolResult, EvidenceDto]:
        evidence = evidence_catalog.get(evidence_id)
        if evidence is None:
            return CallToolResult(
                content=[TextContent(text="evidence is not published")],
                isError=True,
            )
        return _evidence_call_result(EvidenceDto.model_validate(asdict(evidence)))

    return server
