"""Run the pinned BGE exact-dense baseline against Golden Set v1."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import torch
from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.dense_retrieval import evaluate_dense_retrieval
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex


CHUNKS_PATH = ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.jsonl"
GOLDEN_PATH = ROOT / "data" / "evaluation" / "golden-set-v1.json"
OUTPUT_PATH = (
    ROOT
    / "data"
    / "eval_results"
    / "m3-bge-small-zh-v1.5-exact-dense-v1.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=QdrantClient(":memory:"),
        collection_name="m3_bge_small_zh_v15_exact_dense_v1",
        vector_size=embedder.dimension,
    )

    started = time.perf_counter()
    report = evaluate_dense_retrieval(
        chunks,
        golden["cases"],
        embedder,
        index,
        limit=5,
    )
    report["artifact"] = {
        "artifact_id": "m3-bge-small-zh-v1.5-exact-dense-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_chunks": CHUNKS_PATH.relative_to(ROOT).as_posix(),
        "corpus_chunks_sha256": _sha256(CHUNKS_PATH),
        "golden_set": GOLDEN_PATH.relative_to(ROOT).as_posix(),
        "golden_set_sha256": _sha256(GOLDEN_PATH),
        "golden_set_id": golden["golden_set_id"],
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "runtime": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": version("transformers"),
            "qdrant_client": version("qdrant-client"),
            "device": "cpu",
            "qdrant_mode": "local_in_memory_exact_brute_force",
        },
        "document_token_diagnostics": embedder.inspect_documents(
            [chunk["text"] for chunk in chunks]
        ),
        "limitations": [
            "Qdrant local mode is exact brute-force; Docker service exact=true remains unverified.",
            "Only BGE small Chinese has been run; cross-model selection is incomplete.",
            "The 12-case Golden Set is too small for general model-quality claims.",
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["artifact"]["document_token_diagnostics"], ensure_ascii=False, indent=2))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
