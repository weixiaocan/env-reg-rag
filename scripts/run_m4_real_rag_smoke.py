"""Run one reproducible Qdrant-to-Zhipu M4 RAG smoke case."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.llm_provider import LlmProviderSettings, build_answer_generator
from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import QueryRequest
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex


OUTPUT_PATH = ROOT / "data" / "eval_results" / "m4-zhipu-real-rag-smoke-v1.json"


async def main() -> None:
    load_dotenv(ROOT / ".env")
    settings = LlmProviderSettings.from_env()
    if settings.provider != "zhipu":
        raise SystemExit("This baseline requires LLM_PROVIDER=zhipu")

    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    client = QdrantClient(url=qdrant_url, timeout=30)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name="corpus_current",
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    numeric_range_index = NumericRangeIndex.from_artifacts(
        evidence_units_path=(
            ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
        ),
        corpus_manifest_path=ROOT / "data" / "registry" / "formal-corpus-v1.json",
    )
    service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=index,
            embedder=embedder,
            numeric_range_index=numeric_range_index,
            limit=5,
        ),
        answer_generator=build_answer_generator(settings),
    )
    request = QueryRequest(
        request_id="m4-real-rag-smoke-ai-007",
        question="武汉市混错接改造项目的评估周期有什么要求？",
        conversation_context={"jurisdiction": "武汉"},
        corpus_version="formal-corpus-v1",
        caller_type="evaluation",
    )

    started = time.perf_counter()
    result = await service.execute(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    evidence_ids = [item.evidence_id for item in result.evidence]
    report = {
        "evaluation_id": "m4-zhipu-real-rag-smoke-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "qdrant_collection_alias": "corpus_current",
        "retrieval_mode": "rrf",
        "elapsed_ms": elapsed_ms,
        "expected_evidence_id": "ev_d6a4f70024ff1539091b28cdf9a66bf3",
        "expected_evidence_retrieved": (
            "ev_d6a4f70024ff1539091b28cdf9a66bf3" in evidence_ids
        ),
        "result": asdict(result),
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "provider": settings.provider,
                "model": settings.model,
                "elapsed_ms": elapsed_ms,
                "expected_evidence_retrieved": report[
                    "expected_evidence_retrieved"
                ],
                "claim_evidence_ids": [
                    claim.evidence_ids for claim in result.answer.claims
                ]
                if result.answer
                else [],
                "report": str(OUTPUT_PATH),
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
