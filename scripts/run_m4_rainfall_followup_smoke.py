"""Run the real two-turn rainfall clarification and answer smoke case."""

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
from src.domain.query import QueryRequest, QueryStatus
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex


OUTPUT_PATH = ROOT / "data" / "eval_results" / "m4-zhipu-rainfall-followup-v1.json"
EXPECTED_EVIDENCE_ID = "ev_18de8c71615152558e1368e48ee73d1d"


async def main() -> None:
    load_dotenv(ROOT / ".env")
    settings = LlmProviderSettings.from_env()
    if settings.provider != "zhipu":
        raise SystemExit("This smoke case requires LLM_PROVIDER=zhipu")

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=QdrantClient(
            url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
        ),
        collection_name="corpus_current",
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    numeric_index = NumericRangeIndex.from_artifacts(
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
            numeric_range_index=numeric_index,
            limit=5,
        ),
        answer_generator=build_answer_generator(settings),
    )

    first = await service.execute(
        QueryRequest(
            request_id="m4-rainfall-followup-turn-1",
            question="降水量为20毫米时属于什么等级？",
            conversation_context={},
            corpus_version="formal-corpus-v1",
            caller_type="evaluation",
        )
    )
    started = time.perf_counter()
    second = await service.execute(
        QueryRequest(
            request_id="m4-rainfall-followup-turn-2",
            question="12小时",
            conversation_context=first.continuation_context,
            corpus_version="formal-corpus-v1",
            caller_type="evaluation",
        )
    )
    second_elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    cited_ids = {
        evidence_id
        for claim in second.answer.claims if second.answer
        for evidence_id in claim.evidence_ids
    }
    passed = (
        first.status == QueryStatus.NEEDS_CLARIFICATION
        and first.missing_conditions == ["statistical_period"]
        and second.status == QueryStatus.ANSWERED
        and second.resolved_scope.get("statistical_period") == "12h"
        and EXPECTED_EVIDENCE_ID in cited_ids
    )
    report = {
        "evaluation_id": "m4-zhipu-rainfall-followup-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "model": settings.model,
        "corpus_version": "formal-corpus-v1",
        "qdrant_collection_alias": "corpus_current",
        "second_turn_elapsed_ms": second_elapsed_ms,
        "expected_evidence_id": EXPECTED_EVIDENCE_ID,
        "passed": passed,
        "turns": [asdict(first), asdict(second)],
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "first_status": first.status,
                "second_status": second.status,
                "second_scope": second.resolved_scope,
                "cited_ids": sorted(cited_ids),
                "second_turn_elapsed_ms": second_elapsed_ms,
                "report": str(OUTPUT_PATH),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
