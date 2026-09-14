"""Run Qdrant BGE + multilingual BM25 reciprocal-rank fusion on Golden Set v1."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.dense_retrieval import evaluate_rrf_retrieval
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex


CHUNKS_PATH = ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.jsonl"
GOLDEN_PATH = ROOT / "data" / "evaluation" / "golden-set-v1.json"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m3-qdrant-bge-bm25-rrf-v1.json"
COLLECTION_NAME = "m3_retrieval_v1_bge_bm25"
PREFETCH_LIMIT = 20


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))
    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    client = QdrantClient(url=qdrant_url, timeout=30)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=COLLECTION_NAME,
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=PREFETCH_LIMIT,
    )

    started = time.perf_counter()
    report = evaluate_rrf_retrieval(
        chunks,
        golden["cases"],
        embedder,
        index,
        limit=5,
    )
    report["artifact"] = {
        "artifact_id": "m3-qdrant-bge-bm25-rrf-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_chunks": CHUNKS_PATH.relative_to(ROOT).as_posix(),
        "corpus_chunks_sha256": _sha256(CHUNKS_PATH),
        "golden_set": GOLDEN_PATH.relative_to(ROOT).as_posix(),
        "golden_set_sha256": _sha256(GOLDEN_PATH),
        "golden_set_id": golden["golden_set_id"],
        "collection_name": COLLECTION_NAME,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "runtime": {
            "python": platform.python_version(),
            "qdrant_client": version("qdrant-client"),
            "qdrant_server": client.info().version,
            "qdrant_url": qdrant_url,
            "qdrant_mode": "self_hosted_docker",
        },
        "retrieval_config": {
            "dense": "BAAI/bge-small-zh-v1.5 exact cosine",
            "sparse": "qdrant/bm25 multilingual",
            "fusion": "reciprocal_rank_fusion",
            "prefetch_limit_per_branch": PREFETCH_LIMIT,
            "result_limit": 5,
        },
        "limitations": [
            "Only 12 Golden Set cases are available.",
            "RRF combines rank positions and does not perform numeric interval reasoning.",
            "The collection is a rebuildable retrieval projection, not the corpus fact source.",
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
