#!/usr/bin/env python
"""V2 full-corpus ingestion into the persistent Qdrant collection (plan B8).

Iterates every V2 Canonical artifact in
``data/canonical/v2/corpus-37456321a968/*.canonical.json`` (21 documents),
runs the V2 structure-aware chunker, embeds each chunk with the local
bge-small-zh model, and builds the persistent Qdrant collection ``corpus_v2``
(dense 512-dim + BM25 sparse, RRF fusion) against the Docker Qdrant instance
at ``http://127.0.0.1:6333``.

Idempotency: the collection is dropped and rebuilt full-c corpus on each run
(``QdrantRetrievalIndex.build`` recreates the collection). Re-running therefore
reflects the latest V2 Canonical artifacts. Incremental upsert-by-chunk_id is
future work (plan B8 范围外).

Usage (from repo root, venv active, Qdrant container up)::

    PYTHONPATH=src python scripts/ingest_corpus_v2.py
    PYTHONPATH=src python scripts/ingest_corpus_v2.py --qdrant-url http://127.0.0.1:6333

Exit code 0 iff the collection point count equals the total chunk count and
the sample cross-document queries return hits.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.v2_chunker import chunk_document  # noqa: E402
from src.domain.v2_chunk import V2Chunk  # noqa: E402

CORPUS_VERSION = "corpus-37456321a968"
CANONICAL_DIR = os.path.join(
    _REPO_ROOT, "data", "canonical", "v2", CORPUS_VERSION
)
COLLECTION = "corpus_v2"
DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"

# Cross-document sample queries for the post-ingest smoke check.
_SAMPLE_QUERIES = [
    "管道流量测量方法",
    "水质特征因子应能区分污水与雨水",
    "雨污混接程度分级",
]


def _iter_canonical_paths() -> list[str]:
    if not os.path.isdir(CANONICAL_DIR):
        raise FileNotFoundError(f"canonical dir not found: {CANONICAL_DIR}")
    paths = sorted(
        os.path.join(CANONICAL_DIR, name)
        for name in os.listdir(CANONICAL_DIR)
        if name.endswith(".canonical.json")
    )
    if not paths:
        raise FileNotFoundError(f"no .canonical.json artifacts in {CANONICAL_DIR}")
    return paths


def _collect_chunks(paths: list[str]) -> tuple[list[V2Chunk], dict[str, Any]]:
    """Chunk every canonical document; return (chunks, per-doc report)."""
    all_chunks: list[V2Chunk] = []
    per_doc: list[dict[str, Any]] = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            canonical = json.load(fh)
        sha = canonical.get("source", {}).get("sha256", "")
        file_name = canonical.get("source", {}).get("file_name", "")
        chunks = chunk_document(canonical)
        all_chunks.extend(chunks)
        pages = {span.physical_page for c in chunks for span in c.source_spans}
        per_doc.append({
            "sha": sha,
            "file_name": file_name,
            "chunk_count": len(chunks),
            "page_count": len(pages),
        })
    return all_chunks, {"documents": len(paths), "per_doc": per_doc}


def _build_collection(
    chunks: list[V2Chunk], qdrant_url: str
) -> dict[str, Any]:
    """Embed chunks and build the persistent Qdrant collection."""
    from qdrant_client import QdrantClient  # noqa: E402

    from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
    from src.retrieval.qdrant_index import (  # noqa: E402
        HybridQuery,
        QdrantRetrievalIndex,
    )

    report: dict[str, Any] = {
        "collection": COLLECTION,
        "qdrant_url": qdrant_url,
        "vector_size": BgeSmallZhEmbedder.dimension,
        "build_status": "not_run",
        "vector_count": 0,
        "point_count": 0,
    }

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    payloads = [c.to_qdrant_payload() for c in chunks]

    t0 = time.time()
    vectors = embedder.embed_documents([c.text for c in chunks])
    report["embed_seconds"] = round(time.time() - t0, 2)
    report["vector_count"] = len(vectors)
    if len(vectors) != len(payloads):
        raise RuntimeError(
            f"vector/payload count mismatch: {len(vectors)} vs {len(payloads)}"
        )

    client = QdrantClient(url=qdrant_url)
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=COLLECTION,
        vector_size=BgeSmallZhEmbedder.dimension,
        enable_bm25=True,
    )
    t1 = time.time()
    index.build(payloads, vectors)
    report["build_seconds"] = round(time.time() - t1, 2)
    report["build_status"] = "ok"

    info = client.get_collection(COLLECTION)
    report["point_count"] = info.points_count
    report["search_smoke"] = _smoke_queries(client, index, embedder)
    return report


def _smoke_queries(
    client: Any, index: Any, embedder: Any
) -> list[dict[str, Any]]:
    """Run a few cross-document RRF queries; record top hit per query."""
    from src.retrieval.qdrant_index import HybridQuery  # noqa: E402

    out: list[dict[str, Any]] = []
    for query in _SAMPLE_QUERIES:
        dense = embedder.embed_query(query)
        hits = index.search(
            HybridQuery(text=query, dense_vector=dense),
            mode="rrf",
            limit=5,
        )
        top = hits[0] if hits else None
        out.append({
            "query": query,
            "hit_count": len(hits),
            "top_chunk_id": top.chunk_id if top else None,
            "top_file_name": top.file_name if top else None,
            "top_score": round(top.score, 4) if top else None,
            "top_text_head": (top.text[:60] if top else None),
        })
    return out


def _write_report(report: dict[str, Any]) -> str:
    out_path = os.path.join(
        _REPO_ROOT, "data", "canonical", "v2", CORPUS_VERSION, "ingest_report.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return out_path


def ingest(qdrant_url: str = DEFAULT_QDRANT_URL) -> dict[str, Any]:
    paths = _iter_canonical_paths()
    print(f"[ingest] {len(paths)} canonical documents in {CANONICAL_DIR}")
    chunks, chunk_report = _collect_chunks(paths)
    total = len(chunks)
    print(f"[ingest] chunked {total} chunks across {chunk_report['documents']} docs")

    build_report = _build_collection(chunks, qdrant_url)
    report = {
        "corpus_version": CORPUS_VERSION,
        "collection": COLLECTION,
        "total_chunks": total,
        "chunk_report": chunk_report,
        "build": build_report,
    }

    ok = (
        build_report["build_status"] == "ok"
        and build_report["point_count"] == total
        and all(q["hit_count"] > 0 for q in build_report.get("search_smoke", []))
    )
    report["status"] = "ok" if ok else "incomplete"
    out_path = _write_report(report)
    print(f"[ingest] report written to {out_path}")
    return report


def _print_report(report: dict[str, Any]) -> None:
    b = report["build"]
    print("=" * 72)
    print("V2 Corpus Ingestion (plan B8)")
    print("=" * 72)
    print(f"  corpus_version   : {report['corpus_version']}")
    print(f"  collection       : {report['collection']}")
    print(f"  documents        : {report['chunk_report']['documents']}")
    print(f"  total_chunks     : {report['total_chunks']}")
    print(f"  vector_count     : {b['vector_count']}")
    print(f"  embed_seconds    : {b.get('embed_seconds')}")
    print(f"  build_seconds    : {b.get('build_seconds')}")
    print(f"  point_count      : {b['point_count']}")
    print(f"  build_status     : {b['build_status']}")
    print(f"  overall_status   : {report['status']}")
    print("-" * 72)
    print("Cross-document RRF smoke queries")
    print("-" * 72)
    for q in b.get("search_smoke", []):
        print(f"  q={q['query']!r}")
        print(f"    hits={q['hit_count']} top={q['top_file_name']} "
              f"score={q['top_score']} :: {q['top_text_head']!r}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    args = parser.parse_args(argv)
    report = ingest(args.qdrant_url)
    _print_report(report)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
