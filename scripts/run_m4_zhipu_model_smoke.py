"""One paid-call smoke test for the M4 Zhipu answer-model adapter."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.llm_provider import (
    LlmConfigurationError,
    LlmProviderSettings,
    build_answer_generator,
)
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import EvidenceItem, QueryRequest


async def main() -> None:
    load_dotenv()
    try:
        settings = LlmProviderSettings.from_env()
    except LlmConfigurationError as exc:
        raise SystemExit(f"LLM configuration error: {exc}") from exc
    if settings.provider != "zhipu":
        raise SystemExit("This smoke test requires LLM_PROVIDER=zhipu")

    evidence = EvidenceItem(
        evidence_id="smoke-evidence-001",
        text="评估周期应至少跨越一个完整雨季。",
        document_version_id="smoke-document-001",
        file_name="模型接入冒烟测试证据.pdf",
        physical_pages=[1],
        heading_path=["评估周期"],
        usage_policy="answer_and_citation",
        source_uri="https://example.org/smoke-source.pdf",
    )
    service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=InMemoryEvidenceRetriever([evidence]),
        answer_generator=build_answer_generator(settings),
    )
    result = await service.execute(
        QueryRequest(
            request_id="zhipu-model-smoke-001",
            question="评估周期有什么要求？",
            conversation_context={},
            corpus_version="model-smoke-only",
            caller_type="cli",
        )
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
