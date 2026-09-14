"""Evaluate deterministic numeric-range promotion on the published corpus."""

from __future__ import annotations

import asyncio
import hashlib
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
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.numeric_ranges import NumericRangeIndex
from src.retrieval.qdrant_index import QdrantRetrievalIndex


EVIDENCE_PATH = ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
MANIFEST_PATH = ROOT / "data" / "registry" / "formal-corpus-v1.json"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m4-numeric-range-retrieval-v1.json"
EXPECTED_EVIDENCE_ID = "ev_18de8c71615152558e1368e48ee73d1d"
CASES = [
    {
        "case_id": "AI-001",
        "question": "24小时降水量为20毫米时属于什么等级？",
        "scope": {"statistical_period": "24h"},
        "expected_category": "中雨",
    },
    {
        "case_id": "AI-002",
        "question": "12小时降水量为20毫米时属于什么等级？",
        "scope": {"statistical_period": "12h"},
        "expected_category": "大雨",
    },
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main() -> None:
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    numeric_index = NumericRangeIndex.from_artifacts(
        evidence_units_path=EVIDENCE_PATH,
        corpus_manifest_path=MANIFEST_PATH,
    )
    index = QdrantRetrievalIndex(
        client=QdrantClient(
            url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
        ),
        collection_name="corpus_current",
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    retriever = QdrantEvidenceRetriever(
        index=index,
        embedder=embedder,
        numeric_range_index=numeric_index,
        limit=5,
    )

    results = []
    for case in CASES:
        matches = numeric_index.match(
            question=case["question"],
            resolved_scope=case["scope"],
        )
        evidence_pack = await retriever.retrieve(
            question=case["question"],
            resolved_scope=case["scope"],
            corpus_version="formal-corpus-v1",
        )
        rankings = [item.evidence_id for item in evidence_pack.evidence]
        matched_categories = [match.category for match in matches]
        passed = (
            rankings[:1] == [EXPECTED_EVIDENCE_ID]
            and matched_categories == [case["expected_category"]]
        )
        results.append(
            {
                **case,
                "matched_categories": matched_categories,
                "rankings": rankings,
                "target_rank": (
                    rankings.index(EXPECTED_EVIDENCE_ID) + 1
                    if EXPECTED_EVIDENCE_ID in rankings
                    else None
                ),
                "passed": passed,
            }
        )

    report = {
        "evaluation_id": "m4-numeric-range-retrieval-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "corpus_version": "formal-corpus-v1",
        "qdrant_collection_alias": "corpus_current",
        "retrieval_mode": "numeric_range_promotion_then_qdrant_rrf",
        "evidence_units_sha256": _sha256(EVIDENCE_PATH),
        "corpus_manifest_sha256": _sha256(MANIFEST_PATH),
        "passed_cases": sum(case["passed"] for case in results),
        "total_cases": len(results),
        "all_passed": all(case["passed"] for case in results),
        "cases": results,
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
