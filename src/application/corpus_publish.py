"""Publish a validated corpus candidate as an immutable Qdrant collection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _embedding_vectors(
    *, root: Path, chunks: list[dict[str, Any]], embedder: Any
) -> list[list[float]]:
    model_id = str(getattr(embedder, "model_id", type(embedder).__name__))
    cache_path = root / "data" / "model_runtime" / "embedding-cache.jsonl"
    cached: dict[tuple[str, str, str], list[float]] = {}
    if cache_path.is_file():
        for item in _read_jsonl(cache_path):
            cached[(item["model_id"], item["chunk_id"], item["text_sha256"])] = list(
                item["vector"]
            )
    keys = [
        (model_id, str(chunk["chunk_id"]), _text_sha(str(chunk["text"])))
        for chunk in chunks
    ]
    missing_indices = [index for index, key in enumerate(keys) if key not in cached]
    if missing_indices:
        new_vectors = embedder.embed_documents(
            [str(chunks[index]["text"]) for index in missing_indices]
        )
        if len(new_vectors) != len(missing_indices):
            raise RuntimeError("embedder returned an unexpected vector count")
        for index, vector in zip(missing_indices, new_vectors, strict=True):
            if len(vector) != embedder.dimension:
                raise RuntimeError("embedder returned an unexpected vector dimension")
            cached[keys[index]] = list(vector)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for (entry_model, chunk_id, text_sha), vector in sorted(cached.items()):
                handle.write(
                    json.dumps(
                        {
                            "model_id": entry_model,
                            "chunk_id": chunk_id,
                            "text_sha256": text_sha,
                            "vector": vector,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        temporary.replace(cache_path)
    return [cached[key] for key in keys]


def publish_corpus_candidate(
    project_root: Path,
    *,
    client: Any,
    embedder: Any,
    alias_name: str = "corpus_current",
    enable_bm25: bool = True,
) -> dict[str, Any]:
    """Publish only a complete candidate; write the current pointer after alias swap."""

    root = Path(project_root).resolve()
    candidate_path = root / "data" / "registry" / "corpus-candidate.json"
    candidate = _read_json(candidate_path)
    manifest_path = root / candidate["manifest"]
    actual_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if actual_manifest_sha != candidate.get("manifest_sha256"):
        raise ValueError("candidate manifest SHA-256 does not match")
    manifest = _read_json(manifest_path)
    if candidate.get("status") != "ready" or manifest.get("status") != "ready":
        raise ValueError("corpus candidate is not ready for publication")
    if candidate["corpus_version"] != manifest["corpus_version"]:
        raise ValueError("candidate pointer and manifest versions do not match")
    chunks_path = root / manifest["retrieval_chunks"]
    actual_chunks_sha = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    if actual_chunks_sha != manifest.get("retrieval_chunks_sha256"):
        raise ValueError("retrieval chunks SHA-256 does not match candidate manifest")
    chunks = _read_jsonl(chunks_path)
    if len(chunks) != int(manifest["chunk_count"]):
        raise ValueError("retrieval chunk count does not match candidate manifest")
    if not chunks:
        raise ValueError("cannot publish an empty corpus")

    collection_name = manifest["corpus_version"].replace("-", "_")
    current_path = root / "data" / "registry" / "corpus-current.json"
    current = _read_json(current_path) if current_path.is_file() else {}
    alias_targets = {
        item.collection_name
        for item in client.get_aliases().aliases
        if item.alias_name == alias_name
    }
    collection_is_published = (
        current.get("corpus_version") == manifest["corpus_version"]
        or collection_name in alias_targets
    )
    needs_build = True
    if client.collection_exists(collection_name):
        count = client.count(collection_name, exact=True).count
        if count != len(chunks):
            if collection_is_published:
                raise RuntimeError(
                    f"immutable collection {collection_name} has {count} points; "
                    f"expected {len(chunks)}"
                )
            client.delete_collection(collection_name)
            action = "rebuilt"
        else:
            action = "validated"
            needs_build = False
    else:
        action = "built"
    if needs_build:
        vectors = _embedding_vectors(root=root, chunks=chunks, embedder=embedder)
        QdrantRetrievalIndex(
            client=client,
            collection_name=collection_name,
            vector_size=embedder.dimension,
            enable_bm25=enable_bm25,
        ).build(chunks, vectors)
        count = client.count(collection_name, exact=True).count
        if count != len(chunks):
            raise RuntimeError("published collection failed point-count verification")

    QdrantCorpusReleaseManager(client=client).publish(
        collection_name=collection_name,
        alias_name=alias_name,
    )
    result = {
        "schema_version": "1",
        "status": "published",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "corpus_version": manifest["corpus_version"],
        "manifest": candidate["manifest"],
        "collection_name": collection_name,
        "query_alias": alias_name,
        "chunk_count": len(chunks),
        "action": action,
    }
    _write_json_atomic(current_path, result)
    return result
