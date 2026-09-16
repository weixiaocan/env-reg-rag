"""Idempotently materialize the two Qdrant projections required by the app."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_artifacts import resolve_current_corpus
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


EXPERIMENT_CHUNKS = ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.jsonl"
EXPERIMENT_MANIFEST = (
    ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.manifest.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_chunks(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def alias_target(client: QdrantClient, alias_name: str) -> str | None:
    return next(
        (
            alias.collection_name
            for alias in client.get_aliases().aliases
            if alias.alias_name == alias_name
        ),
        None,
    )


def ensure_projection(
    *,
    client: QdrantClient,
    embedder: BgeSmallZhEmbedder,
    chunks: list[dict],
    collection_name: str,
    alias_name: str,
) -> str:
    if client.collection_exists(collection_name):
        indexed_count = client.count(collection_name, exact=True).count
        if indexed_count != len(chunks):
            raise RuntimeError(
                f"{collection_name} has {indexed_count} points; expected {len(chunks)}"
            )
        action = "validated"
    else:
        index = QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=embedder.dimension,
            enable_bm25=True,
        )
        index.build(
            chunks,
            embedder.embed_documents([chunk["text"] for chunk in chunks]),
        )
        action = "built"

    if alias_target(client, alias_name) != collection_name:
        QdrantCorpusReleaseManager(client=client).publish(
            collection_name=collection_name,
            alias_name=alias_name,
        )
        action += "_and_published"
    return action


def main() -> None:
    current = resolve_current_corpus(ROOT)
    formal_manifest = json.loads(current.manifest_path.read_text(encoding="utf-8"))
    if formal_manifest["status"] != "ready":
        raise RuntimeError("current corpus manifest has not passed its build gate")
    expected_sha = (
        formal_manifest.get("retrieval_chunks_sha256")
        or formal_manifest.get("inputs", {}).get("formal_retrieval_chunks_sha256")
    )
    if not expected_sha or sha256(current.retrieval_chunks_path) != expected_sha:
        raise RuntimeError("current corpus chunks do not match their manifest")

    experiment_manifest = json.loads(
        EXPERIMENT_MANIFEST.read_text(encoding="utf-8")
    )
    formal_chunks = load_chunks(current.retrieval_chunks_path)
    experiment_chunks = load_chunks(EXPERIMENT_CHUNKS)
    expected_count = formal_manifest.get(
        "chunk_count", formal_manifest.get("included_chunk_count")
    )
    if len(formal_chunks) != expected_count:
        raise RuntimeError("current corpus chunk count does not match its manifest")
    if len(experiment_chunks) != experiment_manifest["summary"]["chunk_count"]:
        raise RuntimeError("experiment chunk count does not match its manifest")

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=30
    )
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    outcomes = {
        "formal": ensure_projection(
            client=client,
            embedder=embedder,
            chunks=formal_chunks,
            collection_name=current.collection_name,
            alias_name="corpus_current",
        ),
        "experiment": ensure_projection(
            client=client,
            embedder=embedder,
            chunks=experiment_chunks,
            collection_name="m3_experiment_release_v1",
            alias_name="m3_experiment_current",
        ),
    }
    print(json.dumps({"status": "ready", "outcomes": outcomes}, sort_keys=True))


if __name__ == "__main__":
    main()
