"""Run a no-LLM retrieval preflight for the M4 answer set."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import QueryRequest
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex


ANSWER_SET_PATH = ROOT / "data" / "evaluation" / "m4-answer-set-v1.json"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m4-answer-preflight-v1.json"


async def main() -> None:
    answer_set = json.loads(ANSWER_SET_PATH.read_text(encoding="utf-8"))
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
    )
    numeric_index = NumericRangeIndex.from_artifacts(
        evidence_units_path=(
            ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
        ),
        corpus_manifest_path=ROOT / "data" / "registry" / "formal-corpus-v1.json",
    )
    formal_retriever = QdrantEvidenceRetriever(
        index=QdrantRetrievalIndex(
            client=client,
            collection_name="corpus_current",
            vector_size=embedder.dimension,
            enable_bm25=True,
        ),
        embedder=embedder,
        numeric_range_index=numeric_index,
        limit=5,
    )
    locator_retriever = QdrantEvidenceRetriever(
        index=QdrantRetrievalIndex(
            client=client,
            collection_name="m3_experiment_current",
            vector_size=embedder.dimension,
            enable_bm25=True,
        ),
        embedder=embedder,
        limit=1,
    )
    resolver = RuleBasedScopeResolver()
    results = []
    for case in answer_set["cases"]:
        request = QueryRequest(
            request_id=f"m4-preflight-{case['case_id'].lower()}",
            question=case["question"],
            conversation_context=case.get("context", {}),
            corpus_version=(
                "m3-experiment-v1"
                if case["retrieval_profile"] == "locator"
                else "formal-corpus-v1"
            ),
            caller_type="evaluation",
        )
        scope = await resolver.resolve(request)
        if case["expected_status"] == "needs_clarification":
            expected_missing = case.get("expected_missing_conditions", [])
            results.append(
                {
                    "case_id": case["case_id"],
                    "expected_status": case["expected_status"],
                    "resolved_scope": scope.resolved_scope,
                    "missing_conditions": scope.missing_conditions,
                    "preflight_result": (
                        "passed"
                        if scope.missing_conditions == expected_missing
                        else "failed"
                    ),
                }
            )
            continue

        retriever = (
            locator_retriever
            if case["retrieval_profile"] == "locator"
            else formal_retriever
        )
        pack = await retriever.retrieve(
            question=scope.effective_question or case["question"],
            resolved_scope=scope.resolved_scope,
            corpus_version=request.corpus_version,
        )
        evidence_ids = [item.evidence_id for item in pack.evidence]
        usage_policies = [item.usage_policy for item in pack.evidence]
        accepted_ids = set(case.get("accepted_evidence_ids", []))
        if case["expected_status"] == "answered":
            preflight_result = (
                "passed" if accepted_ids.intersection(evidence_ids) else "failed"
            )
        elif case["expected_status"] == "search_only":
            preflight_result = (
                "passed"
                if evidence_ids[:1] and evidence_ids[0] in accepted_ids
                and usage_policies == ["source_locator_only"]
                else "failed"
            )
        elif case["expected_status"] == "refused" and not evidence_ids:
            preflight_result = "passed"
        else:
            preflight_result = "generation_required"
        results.append(
            {
                "case_id": case["case_id"],
                "expected_status": case["expected_status"],
                "resolved_scope": scope.resolved_scope,
                "evidence_ids": evidence_ids,
                "usage_policies": usage_policies,
                "preflight_result": preflight_result,
            }
        )

    report = {
        "evaluation_id": "m4-answer-preflight-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "answer_set_id": answer_set["answer_set_id"],
        "llm_calls": 0,
        "passed": sum(item["preflight_result"] == "passed" for item in results),
        "failed": sum(item["preflight_result"] == "failed" for item in results),
        "generation_required": sum(
            item["preflight_result"] == "generation_required" for item in results
        ),
        "cases": results,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
