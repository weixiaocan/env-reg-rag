#!/usr/bin/env python
"""V2 Chunk traceability-chain verification + local Qdrant ingestion (plan 2.3).

Loads the CECS758 V2 Canonical Document, runs the V2 Chunker, then for every
chunk walks the full provenance chain (design §9.3)::

    chunk -> canonical_id + element_ids -> Canonical Element.content
          -> source_spans -> PDF sha256 + physical_page + bbox

and asserts the chain is unbroken at every layer. Then it embeds the chunks
with the local bge-small-zh model, builds an in-memory Qdrant collection via
``QdrantRetrievalIndex.build``, and runs a few sample queries to confirm the
chunks are retrievable (dense + bm25 hybrid via RRF).

Output: a report on stdout + a JSON artifact at
``data/canonical/v2/{corpus_version}/{sha}.chunk_trace.json`` with chunk counts,
per-element-type contribution, trace-chain completeness rate, and Qdrant
build/search status. Exit code 0 iff the trace chain is 100% complete AND the
Qdrant collection is buildable + searchable.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/verify_v2_chunk_trace.py
    PYTHONPATH=src python scripts/verify_v2_chunk_trace.py --sha <sha> \\
        --corpus-version <ver>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

# Make ``src`` importable when run as a plain script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.v2_chunker import chunk_document  # noqa: E402
from src.application.canonical_assembly import CECS758_SHA  # noqa: E402
from src.domain.v2_chunk import V2Chunk  # noqa: E402

DEFAULT_CORPUS_VERSION = "corpus-37456321a968"
DEFAULT_SHA = CECS758_SHA

_COLLECTION = "corpus_v2_cecs758"
_SAMPLE_QUERIES = [
    "管道流量测量方法有哪些？",
    "混接程度分级标准是什么？",
    "水质特征因子应能区分污水与雨水",
]


def _artifact_path(corpus_version: str, sha: str) -> str:
    return os.path.join(
        _REPO_ROOT, "data", "canonical", "v2", corpus_version, f"{sha}.canonical.json"
    )


def _load_canonical(sha: str, corpus_version: str) -> dict[str, Any]:
    path = _artifact_path(corpus_version, sha)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"V2 canonical artifact not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Trace-chain verification (design §9.3)
# --------------------------------------------------------------------------- #

def _verify_trace_chain(
    canonical: dict[str, Any], chunks: list[V2Chunk]
) -> dict[str, Any]:
    """Walk chunk -> canonical_id + element_ids -> Element.content -> source_spans
    -> PDF sha + page + bbox. Return a per-layer pass/fail report.

    The chain is "unbroken" for a chunk iff every layer resolves:
      * chunk.canonical_id == canonical.canonical_id (audit snapshot identity)
      * every chunk.element_id resolves to a canonical Element
      * that Element has source_spans, and every chunk.source_span (page+bbox)
        is drawn from one of its Elements' source_spans
      * every source_span.physical_page resolves to a canonical Page, whose
        source.sha256 backs the PDF (the PDF SHA + page + bbox terminus).
    """
    canonical_id = canonical["canonical_id"]
    source_sha = (canonical.get("source") or {}).get("sha256", "")
    elements_by_id = {e["element_id"]: e for e in canonical.get("elements") or []}
    pages_by_number = {p["physical_page"]: p for p in canonical.get("pages") or []}

    per_chunk: list[dict[str, Any]] = []
    layer_pass = {
        "canonical_id_match": 0,
        "element_ids_resolve": 0,
        "source_spans_in_elements": 0,
        "pages_resolve_to_pdf": 0,
    }
    broken: list[dict[str, Any]] = []

    for chunk in chunks:
        cid_ok = chunk.canonical_id == canonical_id
        if cid_ok:
            layer_pass["canonical_id_match"] += 1

        # element_ids -> Element.content
        missing_eids = [eid for eid in chunk.element_ids if eid not in elements_by_id]
        eids_ok = not missing_eids
        if eids_ok:
            layer_pass["element_ids_resolve"] += 1

        # chunk.source_spans (page+bbox) drawn from the chunk's elements' spans
        element_span_keys: set[tuple[int, tuple[float, ...]]] = set()
        for eid in chunk.element_ids:
            e = elements_by_id.get(eid)
            if not e:
                continue
            for s in e.get("source_spans") or []:
                element_span_keys.add((s["physical_page"], tuple(s["bbox"])))
        spans_ok = True
        span_misses: list[dict[str, Any]] = []
        for s in chunk.source_spans:
            key = (s.physical_page, tuple(s.bbox))
            if key not in element_span_keys:
                spans_ok = False
                span_misses.append({"physical_page": s.physical_page, "bbox": s.bbox})
        if spans_ok:
            layer_pass["source_spans_in_elements"] += 1

        # pages -> PDF (page exists in canonical pages, which back source.sha256)
        pages_ok = True
        page_misses: list[int] = []
        for s in chunk.source_spans:
            if s.physical_page not in pages_by_number:
                pages_ok = False
                page_misses.append(s.physical_page)
        if pages_ok:
            layer_pass["pages_resolve_to_pdf"] += 1

        chain_intact = cid_ok and eids_ok and spans_ok and pages_ok
        entry = {
            "chunk_id": chunk.chunk_id,
            "canonical_id_match": cid_ok,
            "element_ids_resolve": eids_ok,
            "source_spans_in_elements": spans_ok,
            "pages_resolve_to_pdf": pages_ok,
            "chain_intact": chain_intact,
        }
        per_chunk.append(entry)
        if not chain_intact:
            broken.append({
                "chunk_id": chunk.chunk_id,
                "missing_element_ids": missing_eids,
                "span_misses": span_misses,
                "page_misses": page_misses,
            })

    total = len(chunks)
    intact = sum(1 for e in per_chunk if e["chain_intact"])
    return {
        "total_chunks": total,
        "chain_intact": intact,
        "completeness_rate": round(intact / total, 4) if total else 1.0,
        "layer_pass_counts": {k: v for k, v in layer_pass.items()},
        "source_sha256": source_sha,
        "broken_examples": broken[:10],
        "broken_count": len(broken),
    }


# --------------------------------------------------------------------------- #
# Qdrant local ingestion
# --------------------------------------------------------------------------- #

def _build_and_query_qdrant(chunks: list[V2Chunk]) -> dict[str, Any]:
    """Embed chunks with bge-small-zh, build an in-memory Qdrant collection,
    and run sample hybrid (RRF) searches. Return a status report."""
    report: dict[str, Any] = {
        "collection": _COLLECTION,
        "build_status": "not_run",
        "vector_count": 0,
        "queries": [],
        "search_status": "not_run",
    }
    try:
        from qdrant_client import QdrantClient  # noqa: E402

        from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
        from src.retrieval.qdrant_index import (  # noqa: E402
            HybridQuery,
            QdrantRetrievalIndex,
        )
    except Exception as exc:  # pragma: no cover - env-dependent
        report["build_status"] = f"import_error: {exc}"
        report["search_status"] = "skipped"
        return report

    try:
        embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    except Exception as exc:
        report["build_status"] = f"embedder_load_error: {exc}"
        report["search_status"] = "skipped"
        return report

    payloads = [c.to_qdrant_payload() for c in chunks]
    try:
        vectors = embedder.embed_documents([c.text for c in chunks])
    except Exception as exc:
        report["build_status"] = f"embed_error: {exc}"
        report["search_status"] = "skipped"
        return report
    report["vector_count"] = len(vectors)

    # qdrant-client 1.19.0 local-mode cannot unpack ``Bm25Config`` options
    # (``get_or_init_sparse_model`` expects a mapping). A thin subclass that
    # returns ``None`` for bm25 options restores local hybrid (RRF) retrieval.
    # ``_to_hit`` needs no override: the base class now tolerates V2 payloads
    # (``primary_evidence_id``/``usage_policy`` default; ``section_path`` and
    # ``jurisdictions`` are accepted as fallbacks for heading_path/jurisdiction).
    class _LocalQdrantIndex(QdrantRetrievalIndex):
        @staticmethod
        def _bm25_options():  # type: ignore[override]
            return None

    try:
        client = QdrantClient(":memory:")
        index = _LocalQdrantIndex(
            client=client,
            collection_name=_COLLECTION,
            vector_size=BgeSmallZhEmbedder.dimension,
            enable_bm25=True,
        )
        index.build(payloads, vectors)
        report["build_status"] = "ok"
    except Exception as exc:
        report["build_status"] = f"build_error: {exc}"
        report["search_status"] = "skipped"
        return report

    # Sample hybrid searches (RRF: dense + bm25).
    query_reports: list[dict[str, Any]] = []
    searches_ok = 0
    for q in _SAMPLE_QUERIES:
        try:
            dense = embedder.embed_query(q)
            hits = index.search(
                HybridQuery(text=q, dense_vector=dense),
                mode="rrf",
                limit=5,
            )
            qrep = {
                "query": q,
                "status": "ok",
                "hit_count": len(hits),
                "top_chunk_id": hits[0].chunk_id if hits else None,
                "top_text_head": (hits[0].text[:60] if hits else ""),
                "top_score": round(hits[0].score, 4) if hits else None,
            }
            searches_ok += 1
        except Exception as exc:
            qrep = {"query": q, "status": f"error: {exc}", "hit_count": 0}
        query_reports.append(qrep)
    report["queries"] = query_reports
    report["search_status"] = "ok" if searches_ok == len(_SAMPLE_QUERIES) else (
        f"partial ({searches_ok}/{len(_SAMPLE_QUERIES)})"
    )
    return report


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def verify(sha: str = DEFAULT_SHA, corpus_version: str = DEFAULT_CORPUS_VERSION) -> dict[str, Any]:
    canonical = _load_canonical(sha, corpus_version)
    chunks = chunk_document(canonical)

    # Per-element-type contribution.
    type_contrib: Counter[str] = Counter()
    for c in chunks:
        for t in c.metadata.get("element_types", []):
            type_contrib[t] += 1

    trace = _verify_trace_chain(canonical, chunks)
    qdrant = _build_and_query_qdrant(chunks)

    chain_complete = trace["completeness_rate"] == 1.0
    qdrant_ok = qdrant["build_status"] == "ok" and qdrant["search_status"] == "ok"
    exit_code = 0 if (chain_complete and qdrant_ok) else 1

    report = {
        "sha": sha,
        "corpus_version": corpus_version,
        "canonical_id": canonical["canonical_id"],
        "canonical_content_id": canonical["canonical_content_id"],
        "chunk_count": len(chunks),
        "chunk_by_element_type": dict(type_contrib),
        "trace_chain": trace,
        "qdrant": qdrant,
        "chain_complete": chain_complete,
        "qdrant_ok": qdrant_ok,
        "exit_code": exit_code,
    }
    _write_report(report, corpus_version, sha)
    return report


def _write_report(report: dict[str, Any], corpus_version: str, sha: str) -> str:
    out_dir = os.path.join(_REPO_ROOT, "data", "canonical", "v2", corpus_version)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{sha}.chunk_trace.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return out_path


def _print_report(report: dict[str, Any]) -> None:
    print("=" * 72)
    print("V2 Chunk Trace-Chain + Qdrant Ingestion Report (plan 2.3)")
    print("=" * 72)
    print(f"  sha                : {report['sha']}")
    print(f"  canonical_id       : {report['canonical_id']}")
    print(f"  canonical_content  : {report['canonical_content_id']}")
    print(f"  chunk_count        : {report['chunk_count']}")
    print(f"  by element_type    : {report['chunk_by_element_type']}")
    t = report["trace_chain"]
    print(f"  trace_chain        : {t['chain_intact']}/{t['total_chunks']} intact "
          f"(rate {t['completeness_rate']:.2%})")
    print(f"    layer pass       : {t['layer_pass_counts']}")
    print(f"    broken_count     : {t['broken_count']}")
    print(f"    source_sha256    : {t['source_sha256'][:16]}...")
    q = report["qdrant"]
    print(f"  qdrant build       : {q['build_status']} (vectors={q['vector_count']})")
    print(f"  qdrant search      : {q['search_status']}")
    for qr in q.get("queries", []):
        head = qr.get("top_text_head", "")
        print(f"    [{qr['status']}] q={qr['query']!r} hits={qr.get('hit_count', 0)} "
              f"top={head!r}")
    print(f"  chain_complete     : {report['chain_complete']}")
    print(f"  qdrant_ok          : {report['qdrant_ok']}")
    print(f"  exit_code          : {report['exit_code']}")
    verdict = "PASS" if report["exit_code"] == 0 else "FAIL"
    print("=" * 72)
    print(f"VERDICT: {verdict}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", default=DEFAULT_SHA, help="PDF SHA256 (default: CECS758)")
    parser.add_argument("--corpus-version", default=DEFAULT_CORPUS_VERSION)
    args = parser.parse_args(argv)
    report = verify(args.sha, args.corpus_version)
    _print_report(report)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
