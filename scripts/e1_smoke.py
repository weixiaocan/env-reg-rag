"""E1 pre-experiment smoke run -- ≤5 cases, explicitly NOT part of any comparison.

Per the authorized flow: after the scope-locked download, load the model
and tokenizer from the SAME snapshot (recording the confirmed commit), then
run the full E1 composition on the first 5 golden cases only.  Any load or
gate failure stops here -- nothing downstream runs.  The smoke record is
written under a distinct ``diagnostic-exp-e1-smoke-<date>`` directory and
labelled as not comparable to the baseline or the experiment rounds.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_golden_retrieval import _load_cases, _json  # noqa: E402
from scripts.probe_miss_rerank import _binding_problems  # noqa: E402
from scripts.run_e1_rerank_experiment import (  # noqa: E402
    E1_CONFIG,
    REGISTRY_POINTER,
    _build_scorer,
    _embedding_problems,
    run_cases,
)
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
from src.retrieval.qdrant_index import QdrantRetrievalIndex  # noqa: E402
from src.retrieval.rerank import RerankConfig, RerankStage  # noqa: E402

PINNED_SHA = "2cfc18c9415c912f9d8155881c133215df768a70"
SMOKE_CASES = 5


def main() -> None:
    e1 = _json(E1_CONFIG)
    if e1["model_download"]["repo_sha"] != PINNED_SHA:
        raise SystemExit(f"REFUSED: E1 config repo_sha != pinned {PINNED_SHA}")

    # -- 1. local model + tokenizer load from the same snapshot
    rerank_config = RerankConfig(
        model_id=e1["variable"]["model_id"],
        revision=PINNED_SHA,
        device=e1["variable"].get("device", "cpu"),
        max_length=e1["variable"].get("max_length", 512),
    )
    load_started = time.perf_counter()
    score, confirmed_revision = _build_scorer(rerank_config, pinned_revision=PINNED_SHA)
    load_seconds = time.perf_counter() - load_started
    print(f"model loaded in {load_seconds:.1f}s, confirmed revision {confirmed_revision}")

    # -- 2. sanity scores: relevant pair should outscore an irrelevant one
    relevant = score("雨水设计流量如何计算？", "雨水设计流量应按汇水面积与设计暴雨强度计算")
    irrelevant = score("雨水设计流量如何计算？", "本标准适用于城市给水工程规划")
    print(f"sanity scores: relevant={relevant:.4f} irrelevant={irrelevant:.4f}")

    # -- 3. the same gates as the runner (dataset/corpus binding, embedding)
    dataset_path = (ROOT / "eval" / "golden_v2.jsonl").resolve()
    cases = _load_cases(dataset_path)[:SMOKE_CASES]
    current = _json(REGISTRY_POINTER)
    binding_config = {
        "dataset_sha256": e1["dataset_sha256"],
        "corpus_version": e1["corpus_version"],
        "collection": e1["collection"],
        "point_count": e1["unchanged_fields"]["point_count"],
    }
    from qdrant_client import QdrantClient

    client = QdrantClient(url="http://127.0.0.1:6333", timeout=30.0)
    problems = _binding_problems(binding_config, dataset_path, current, client=client)
    if problems:
        raise SystemExit(f"REFUSED: {problems[0]}")
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    embedding_problems = _embedding_problems(e1["unchanged_fields"], embedder)
    if embedding_problems:
        raise SystemExit(f"REFUSED: {embedding_problems[0]}")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=e1["collection"],
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=20,
    )

    # -- 4. smoke the full composition on ≤5 cases
    stage = RerankStage(config=rerank_config, scorer=score)
    started = time.perf_counter()
    results = run_cases(
        cases,
        index=index,
        embedder=embedder,
        stage=stage,
        corpus_version=current["corpus_version"],
        prefetch_limit=20,
        limit=10,
    )
    wall_seconds = time.perf_counter() - started
    record = {
        "classification": "diagnostic",
        "kind": "smoke (≤5 cases, NOT comparable to baseline or experiment rounds)",
        "run_id": f"diagnostic-exp-e1-smoke-{datetime.now(UTC).strftime('%Y%m%d')}",
        "model_id": rerank_config.model_id,
        "confirmed_revision": confirmed_revision,
        "model_load_seconds": round(load_seconds, 3),
        "sanity_scores": {"relevant": relevant, "irrelevant": irrelevant},
        "case_count": len(results),
        "wall_seconds": round(wall_seconds, 3),
        "rerank_latency_ms_per_case": [round(r["rerank_latency_ms"], 1) for r in results],
        "cases": [
            {
                "id": r["id"],
                "recall_at_5": r["metrics"]["recall_at_5"],
                "recall_at_10": r["metrics"]["recall_at_10"],
            }
            for r in results
        ],
    }
    out_dir = ROOT / "eval" / "runs" / record["run_id"]
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "smoke_record.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
