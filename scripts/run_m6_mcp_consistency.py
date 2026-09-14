"""Exercise direct, HTTP and stdio MCP source lookup against the live corpus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import Client, StdioServerParameters

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.query import QueryRequest
from src.server.query_dto import AnswerResultDto
from src.server.runtime import build_query_services


def _digest(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _progress(message: str) -> None:
    print(f"[m6-consistency] {message}", file=sys.stderr, flush=True)


async def run(*, api_url: str, output: Path, python_executable: str) -> dict:
    _progress("loading configuration")
    load_dotenv(PROJECT_ROOT / ".env")
    request_id = "m6-mcp-consistency-formula"
    question = "采用用水量折算法时，原文公式在哪里？"
    expected_evidence_id = "ev_58337104c115f4ddb491ad736f859ae7"
    expected_tools = {
        "answer_with_evidence",
        "get_document_evidence",
        "search_evidence",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    trace_temp = tempfile.TemporaryDirectory(prefix="m6-mcp-consistency-")
    trace_dir = Path(trace_temp.name)
    _progress("building direct query service")
    services = build_query_services(
        project_root=PROJECT_ROOT,
        trace_output_path=trace_dir / "direct.jsonl",
    )

    _progress("running direct source lookup")
    direct_result = await services.source_lookup.execute(
        QueryRequest(
            request_id=request_id,
            question=question,
            conversation_context={},
            corpus_version="m3-experiment-v1",
            caller_type="direct",
        )
    )
    direct = AnswerResultDto.model_validate(asdict(direct_result)).model_dump(
        mode="json"
    )

    _progress("calling live HTTP API")
    async with httpx.AsyncClient(timeout=90) as http:
        response = await http.post(
            f"{api_url.rstrip('/')}/api/v1/queries",
            json={
                "request_id": request_id,
                "question": question,
                "conversation_context": {},
                "mode": "source_lookup",
            },
        )
        response.raise_for_status()
        http_result = response.json()

    stdio = StdioServerParameters(
        command=python_executable,
        args=["-m", "src.mcp_adapter.main"],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "QUERY_TRACE_PATH": str(trace_dir / "mcp.jsonl"),
        },
    )
    _progress("starting independent stdio MCP server")
    async with Client(stdio, raise_exceptions=True, read_timeout_seconds=120) as client:
        _progress("listing and calling MCP tools")
        tools = (await client.list_tools()).tools
        mcp_call = await client.call_tool(
            "search_evidence",
            {
                "request_id": request_id,
                "question": question,
                "conversation_context": {},
            },
            read_timeout_seconds=120,
        )
        resource_link = next(
            (item for item in mcp_call.content if item.type == "resource_link"),
            None,
        )
        resource_result = (
            await client.read_resource(str(resource_link.uri))
            if resource_link is not None
            else None
        )
    _progress("validating and writing report")
    mcp_result = mcp_call.structured_content
    tool_annotations = {
        tool.name: tool.annotations.model_dump(mode="json", by_alias=True)
        for tool in tools
    }
    evidence_ids = [item["evidence_id"] for item in direct["evidence"]]
    resource_payload = (
        json.loads(resource_result.contents[0].text)
        if resource_result is not None
        else None
    )
    checks = {
        "exact_tool_set": {tool.name for tool in tools} == expected_tools,
        "all_tools_read_only": all(
            annotation.get("readOnlyHint") is True
            and annotation.get("destructiveHint") is False
            and annotation.get("openWorldHint") is False
            for annotation in tool_annotations.values()
        ),
        "mcp_call_succeeded": not mcp_call.is_error,
        "expected_status": direct["status"] == "search_only",
        "expected_evidence": evidence_ids == [expected_evidence_id],
        "semantic_results_identical": direct == http_result == mcp_result,
        "text_and_resource_link_returned": [
            item.type for item in mcp_call.content
        ]
        == ["text", "resource_link"],
        "resource_link_readable": (
            resource_payload is not None
            and resource_payload.get("evidence_id") == expected_evidence_id
            and resource_payload.get("usage_policy") == "source_locator_only"
        ),
    }

    report = {
        "schema_version": "m6-mcp-consistency-v1",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "question": question,
        "request_id": request_id,
        "sdk": "mcp==2.2.0",
        "transport": "stdio",
        "python_executable": Path(python_executable).name,
        "tool_names": sorted(tool.name for tool in tools),
        "tool_annotations": tool_annotations,
        "results": {
            "direct": {"status": direct["status"], "digest": _digest(direct)},
            "http": {
                "status": http_result["status"],
                "digest": _digest(http_result),
            },
            "mcp": {
                "status": mcp_result["status"],
                "digest": _digest(mcp_result),
                "content_types": [item.type for item in mcp_call.content],
            },
        },
        "checks": checks,
        "passed": all(checks.values()),
        "evidence_ids": evidence_ids,
        "resource_uri": str(resource_link.uri) if resource_link is not None else None,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    trace_temp.cleanup()
    _progress("passed" if report["passed"] else "failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "data"
        / "eval_results"
        / "m6-mcp-consistency-v1.json",
    )
    args = parser.parse_args()
    report = asyncio.run(
        run(
            api_url=args.api_url,
            output=args.output,
            python_executable=args.python_executable,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit("MCP consistency gate failed")


if __name__ == "__main__":
    main()
