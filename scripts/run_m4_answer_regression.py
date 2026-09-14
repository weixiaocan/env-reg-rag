"""Run the M4 answer set through the real Qdrant-to-Zhipu query services."""

from __future__ import annotations

import asyncio
import argparse
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


ANSWER_SET_PATH = ROOT / "data" / "evaluation" / "m4-answer-set-v1.json"
DEFAULT_OUTPUT_PATH = ROOT / "data" / "eval_results" / "m4-answer-regression-v1.json"


def _evaluate(case: dict, result: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if result["status"] != case["expected_status"]:
        failures.append(
            f"expected status {case['expected_status']}, got {result['status']}"
        )
    if case["expected_status"] == "needs_clarification":
        if result["missing_conditions"] != case.get("expected_missing_conditions", []):
            failures.append("missing conditions did not match")
    elif case["expected_status"] == "answered" and result.get("answer"):
        claims = result["answer"]["claims"]
        answer_text = "\n".join(claim["text"] for claim in claims)
        cited_ids = {
            evidence_id
            for claim in claims
            for evidence_id in claim["evidence_ids"]
        }
        accepted_ids = set(case.get("accepted_evidence_ids", []))
        if not accepted_ids.intersection(cited_ids):
            failures.append("answer did not cite an accepted evidence ID")
        missing_keywords = [
            keyword
            for keyword in case.get("expected_keywords", [])
            if keyword not in answer_text
        ]
        if missing_keywords:
            failures.append(f"answer missed keywords: {missing_keywords}")
    elif case["expected_status"] == "answered":
        failures.append("answered result had no answer")
    elif case["expected_status"] == "search_only":
        returned_ids = {item["evidence_id"] for item in result["evidence"]}
        if not set(case.get("accepted_evidence_ids", [])).intersection(returned_ids):
            failures.append("search-only result missed the expected locator")
    return not failures, failures


async def main(*, case_id: str | None = None) -> None:
    load_dotenv(ROOT / ".env")
    settings = LlmProviderSettings.from_env()
    if settings.provider != "zhipu":
        raise SystemExit("This regression requires LLM_PROVIDER=zhipu")

    answer_set = json.loads(ANSWER_SET_PATH.read_text(encoding="utf-8"))
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
    )
    generator = build_answer_generator(settings)
    numeric_index = NumericRangeIndex.from_artifacts(
        evidence_units_path=(
            ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
        ),
        corpus_manifest_path=ROOT / "data" / "registry" / "formal-corpus-v1.json",
    )
    formal_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=QdrantRetrievalIndex(
                client=client,
                collection_name="corpus_current",
                vector_size=embedder.dimension,
                enable_bm25=True,
            ),
            embedder=embedder,
            numeric_range_index=numeric_index,
            limit=5,
        ),
        answer_generator=generator,
    )
    locator_service = QueryApplicationService(
        scope_resolver=RuleBasedScopeResolver(),
        evidence_retriever=QdrantEvidenceRetriever(
            index=QdrantRetrievalIndex(
                client=client,
                collection_name="m3_experiment_current",
                vector_size=embedder.dimension,
                enable_bm25=True,
            ),
            embedder=embedder,
            limit=1,
        ),
        answer_generator=generator,
    )

    selected_cases = [
        case
        for case in answer_set["cases"]
        if case_id is None or case["case_id"] == case_id
    ]
    if not selected_cases:
        raise SystemExit(f"unknown case ID: {case_id}")
    output_path = (
        ROOT
        / "data"
        / "eval_results"
        / f"m4-answer-regression-{case_id.lower()}-retry-v1.json"
        if case_id
        else DEFAULT_OUTPUT_PATH
    )

    results = []
    for position, case in enumerate(selected_cases, start=1):
        print(
            f"[{position}/{len(selected_cases)}] {case['case_id']} starting",
            flush=True,
        )
        service = locator_service if case["retrieval_profile"] == "locator" else formal_service
        request = QueryRequest(
            request_id=f"m4-regression-{case['case_id'].lower()}",
            question=case["question"],
            conversation_context=case.get("context", {}),
            corpus_version=(
                "m3-experiment-v1"
                if case["retrieval_profile"] == "locator"
                else "formal-corpus-v1"
            ),
            caller_type="evaluation",
        )
        started = time.perf_counter()
        try:
            domain_result = await service.execute(request)
            serialized = asdict(domain_result)
            passed, failures = _evaluate(case, serialized)
            error = None
        except Exception as exc:  # keep later cases observable after one failure
            serialized = None
            passed = False
            failures = [f"{type(exc).__name__}: {exc}"]
            error = type(exc).__name__
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        results.append(
            {
                "case_id": case["case_id"],
                "expected_status": case["expected_status"],
                "passed": passed,
                "failures": failures,
                "error_type": error,
                "elapsed_ms": elapsed_ms,
                "result": serialized,
            }
        )
        actual_status = serialized["status"] if serialized else error
        print(
            f"[{position}/{len(selected_cases)}] {case['case_id']} "
            f"status={actual_status} passed={passed} elapsed_ms={elapsed_ms}",
            flush=True,
        )

    report = {
        "evaluation_id": "m4-answer-regression-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "answer_set_id": answer_set["answer_set_id"],
        "provider": settings.provider,
        "model": settings.model,
        "passed_cases": sum(item["passed"] for item in results),
        "total_cases": len(results),
        "all_passed": all(item["passed"] for item in results),
        "cases": results,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed_cases": report["passed_cases"],
                "total_cases": report["total_cases"],
                "all_passed": report["all_passed"],
                "report": str(output_path),
            },
            ensure_ascii=True,
            indent=2,
        ),
        flush=True,
    )
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id")
    args = parser.parse_args()
    asyncio.run(main(case_id=args.case_id))
