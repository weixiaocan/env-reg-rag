#!/usr/bin/env python
"""V2 full-corpus ingestion into a VERSIONED STAGING Qdrant collection (T06).

T06 state machine: build -> assemble -> ingest -> publish must be verifiable,
non-destructive, fail-closed and rollback-able. Ingest therefore runs in
strict stages:

1. OFFLINE verification (before ANY Qdrant connection): the artifact
   manifest's bound hashes (page-intermediate, every canonical, quality
   report) are re-verified; the chunk manifest is written (candidate-only);
   ``offline_preflight`` validates schema, registry readiness, zero garbled,
   policy invariants and non-publishable markers (``--allow-garbled``
   diagnostics are refused);
2. chunks are embedded into the VERSIONED STAGING collection
   ``corpus_v2__staging__<corpus_version>`` -- the fixed live collection
   ``corpus_v2``, any alias and the ``corpus-current`` pointer are NEVER
   written before the gate passes AND ``--publish`` is requested;
3. the CLOSED publish gate re-runs the full offline preflight, the exact
   canonical file set + aggregate hash, chunk-set identity, count equality
   and the COMPLETE fixed smoke query set;
4. the final ingest report is assembled in memory and written ONCE; the
   ingest manifest binds it into the parent-hash chain
   (artifact_manifest -> ingest_manifest -> publish_gate_record);
5. on ``--publish`` with a PASSED gate, :func:`switch_pointer` re-verifies
   the on-disk gate record and every hash, writes a rollback record, then
   switches the live alias target AND the pointer -- the previous state is
   always recoverable via ``rollback_pointer`` and the old collection is
   never deleted.

The corpus version defaults to the one published in ``corpus-current.json``
(via ``resolve_current_corpus``); override with ``--corpus-version`` to
ingest a freshly assembled candidate.

Usage (from repo root, venv active, Qdrant container up)::

    PYTHONPATH=src python scripts/ingest_corpus_v2.py
    PYTHONPATH=src python scripts/ingest_corpus_v2.py --corpus-version corpus-<new> --publish
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.corpus_artifacts import resolve_current_corpus  # noqa: E402
from src.application.payload_enrichment import build_enrichment  # noqa: E402
from src.application.publish_state import (  # noqa: E402
    FIXED_SMOKE_QUERIES,
    LIVE_ALIAS,
    LIVE_COLLECTION,
    CandidateConflictError,
    OfflineValidationError,
    build_chunk_manifest,
    build_ingest_manifest,
    load_artifact_manifest,
    load_gate_record,
    load_ingest_manifest,
    offline_preflight,
    publish_gate,
    sha256_file,
    staging_collection_name,
    switch_pointer,
    write_candidate,
    write_chunk_manifest,
    write_gate_record,
    write_ingest_manifest,
)
from src.application.v2_chunker import chunk_document  # noqa: E402
from src.domain.v2_chunk import V2Chunk  # noqa: E402

DEFAULT_QDRANT_URL = "http://127.0.0.1:6333"


class QdrantAliasAdmin:
    """Production alias administration for the gated publish switch.

    A tiny duck-typed interface (``current_target`` / ``switch`` /
    ``remove_alias``) so tests can inject a fake; the real implementation
    uses Qdrant alias operations, which switch targets ATOMICALLY on the
    server side.
    """

    def __init__(self, url: str, alias: str = LIVE_ALIAS):
        from qdrant_client import QdrantClient  # noqa: E402

        self._client = QdrantClient(url=url, timeout=60)
        self._alias = alias

    def current_target(self, alias: str) -> str | None:
        for info in self._client.get_aliases().aliases:
            if info.alias_name == alias:
                return info.collection_name
        return None

    def switch(self, alias: str, collection: str) -> None:
        from qdrant_client import models  # noqa: E402

        ops = []
        existing = self.current_target(alias)
        if existing:
            ops.append(models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias)))
        ops.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(
                collection_name=collection, alias_name=alias)))
        # one request -> the alias switch is atomic on the server
        self._client.update_collection_aliases(change_aliases_operations=ops)

    def remove_alias(self, alias: str) -> None:
        from qdrant_client import models  # noqa: E402

        if self.current_target(alias) is None:
            return
        self._client.update_collection_aliases(change_aliases_operations=[
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias))])


def _iter_canonical_paths(corpus_version: str, root: str | os.PathLike) -> list[str]:
    canonical_dir = os.path.join(root, "data", "canonical", "v2", corpus_version)
    if not os.path.isdir(canonical_dir):
        raise FileNotFoundError(f"canonical dir not found: {canonical_dir}")
    paths = sorted(
        os.path.join(canonical_dir, name)
        for name in os.listdir(canonical_dir)
        if name.endswith(".canonical.json")
    )
    if not paths:
        raise FileNotFoundError(f"no .canonical.json artifacts in {canonical_dir}")
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


def _load_canonical_metadata(root: Path, corpus_version: str) -> dict[str, dict]:
    """sha256 -> {file_name, standard_number, effective_status, ...} (offline)."""
    out: dict[str, dict] = {}
    canonical_dir = root / "data" / "canonical" / "v2" / corpus_version
    if not canonical_dir.is_dir():
        return out
    for path in sorted(canonical_dir.glob("*.canonical.json")):
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
        sha = (doc.get("source") or {}).get("sha256", "").lower()
        if len(sha) != 64:
            continue
        md = doc.get("metadata") or {}
        authorities = md.get("issuing_authorities") or []
        std_no = next(
            (item.get("value", "")
             for item in (md.get("identifiers") or [])
             if item.get("scheme") == "standard_number"),
            "",
        )
        out[sha] = {
            "file_name": (doc.get("source") or {}).get("file_name", ""),
            "document_kind": md.get("document_kind") or "",
            "jurisdictions": md.get("jurisdictions") or [],
            "standard_number": std_no,
            "source_authority": authorities[0] if authorities else "",
            "publication_date": md.get("publication_date") or "",
            "effective_from": md.get("effective_from") or "",
            "effective_to": md.get("effective_to") or "",
            "effective_status": md.get("effective_status") or "unknown",
            "effective_status_as_of": md.get("effective_status_as_of") or "",
        }
    return out


def _load_source_evidence_uris(root: Path) -> dict[str, str]:
    """sha256 -> official_source_uri (from observations), empty if absent."""
    out: dict[str, str] = {}
    path = root / "data" / "registry" / "source_evidence.jsonl"
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            sha = (record.get("file_sha256") or "").lower()
            if len(sha) != 64:
                continue
            uri = ""
            for obs in record.get("observations") or []:
                if obs.get("field") == "official_source_uri":
                    uri = obs.get("value") or ""
                    if uri:
                        break
            out[sha] = uri
    return out


def _enrich_chunks(
    chunks: list[V2Chunk], root: Path, corpus_version: str
) -> list[dict[str, Any]]:
    """Offline payload enrichment (T06 requirement 8).

    Idempotent and policy-preserving: an existing ``usage_policy`` is never
    overwritten; a missing one is backfilled with ``source_locator_only``.
    """
    metadata = _load_canonical_metadata(root, corpus_version)
    uris = _load_source_evidence_uris(root)
    payloads = []
    for chunk in chunks:
        payload = chunk.to_qdrant_payload()
        sha = (payload.get("source_sha256") or "").lower()
        enrichment = build_enrichment(
            payload, metadata.get(sha, {}), uris.get(sha, ""))
        payloads.append({**payload, **enrichment})
    return payloads


def _guarded_collections(client: Any, root: Path) -> set[str]:
    """Collections that must NEVER be deleted, recreated or upserted.

    T06-R3 P1-1: the fixed live collection, whatever collection the current
    pointer records and whatever the live alias currently targets. The alias
    read happens here (after the client exists, still before any write).
    """
    guarded = {LIVE_COLLECTION}
    pointer_path = root / "data" / "registry" / "corpus-current.json"
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if pointer.get("collection_name"):
            guarded.add(str(pointer["collection_name"]))
    for info in client.get_aliases().aliases:
        if info.alias_name == LIVE_ALIAS:
            guarded.add(info.collection_name)
    return guarded


def _build_collection(
    chunks: list[V2Chunk],
    qdrant_url: str,
    root: Path | None = None,
    *,
    collection_name: str,
    corpus_version: str = "",
) -> dict[str, Any]:
    """Embed chunks into the VERSIONED STAGING collection (create-only).

    Called ONLY after offline validation has fully passed. The concrete
    target is resolved via :func:`resolve_staging_target`: an existing
    staging collection is reused READ-ONLY after identity verification
    (no re-embed, no upsert); a foreign-content staging collection is never
    touched -- the build proceeds under a fresh immutable identity. The
    fixed live collection, the pointer's collection and the live alias's
    target are guarded and can never be targeted. Promotion happens solely
    through the gated alias/pointer switch.
    """
    from qdrant_client import QdrantClient  # noqa: E402

    from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
    from src.retrieval.qdrant_index import (  # noqa: E402
        QdrantRetrievalIndex,
    )
    from src.application.publish_state import (  # noqa: E402
        resolve_staging_target,
    )

    report: dict[str, Any] = {
        "collection": collection_name,
        "qdrant_url": qdrant_url,
        "build_status": "not_run",
        "vector_count": 0,
        "point_count": 0,
    }

    root_path = Path(root or _REPO_ROOT)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")

    client = QdrantClient(url=qdrant_url)
    target, mode = resolve_staging_target(
        client, corpus_version,
        chunk_count=len(chunks),
        vector_size=BgeSmallZhEmbedder.dimension,
        guarded_collections=_guarded_collections(client, root_path),
    )
    report["collection"] = target
    report["mode"] = mode

    index = QdrantRetrievalIndex(
        client=client,
        collection_name=target,
        vector_size=BgeSmallZhEmbedder.dimension,
        enable_bm25=True,
    )

    if mode == "reuse":
        # identity-verified READ-ONLY reuse: never re-embed, never upsert --
        # an existing staging collection is immutable once built.
        info = client.get_collection(target)
        points = int(info.points_count or 0)
        report.update({
            "build_status": "reused_readonly",
            "vector_count": points,
            "point_count": points,
            "search_smoke": _smoke_queries(client, index, embedder),
        })
        return report

    vectors = embedder.embed_documents([c.text for c in chunks])
    report["vector_count"] = len(vectors)

    payloads = _enrich_chunks(
        chunks, Path(root or _REPO_ROOT), corpus_version)
    if len(vectors) != len(payloads):
        raise RuntimeError(
            f"vector/payload count mismatch: {len(vectors)} vs {len(payloads)}")

    # create-only: a collection that already exists is NEVER deleted and
    # recreated (resolve_staging_target already steered us to a fresh
    # identity in that case -- this is the hard backstop).
    index.build(payloads, vectors, create_only=True)
    report["build_status"] = "ok"

    info = client.get_collection(target)
    report["point_count"] = info.points_count
    report["search_smoke"] = _smoke_queries(client, index, embedder)
    return report


def _smoke_queries(
    client: Any, index: Any, embedder: Any
) -> list[dict[str, Any]]:
    """Run the COMPLETE fixed cross-document RRF query set."""
    from src.retrieval.qdrant_index import HybridQuery  # noqa: E402

    out: list[dict[str, Any]] = []
    for query in FIXED_SMOKE_QUERIES:
        dense = embedder.embed_query(query)
        hits = index.search(
            HybridQuery(text=query, dense_vector=dense),
            mode="rrf",
            limit=10,
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


def ingest(
    qdrant_url: str = DEFAULT_QDRANT_URL,
    corpus_version: str | None = None,
    publish: bool = False,
    root: str | os.PathLike | None = None,
    _builder: Any = None,
    _alias_admin: Any = None,
    _admin_factory: Any = None,
) -> dict[str, Any]:
    """Offline-first ingest into a staging collection + gated publish.

    ``_builder`` injects the Qdrant build step (tests pass a fake; production
    uses :func:`_build_collection`). ``_alias_admin`` injects the alias
    administration (tests pass a fake; production defers creation via
    ``_admin_factory``). ``_admin_factory(url)`` is invoked ONLY after every
    offline check and the publish gate have passed AND publish is requested
    (T06-R3: no Qdrant admin/client is created on an offline failure).
    ``root`` redirects the data tree (tests).
    """
    root_path = Path(root or _REPO_ROOT)
    corpus_version = corpus_version or resolve_current_corpus(root_path).corpus_version
    canonical_dir = root_path / "data" / "canonical" / "v2" / corpus_version
    art_dir = canonical_dir
    paths = _iter_canonical_paths(corpus_version, root_path)
    print(f"[ingest] {len(paths)} canonical documents in {canonical_dir}")

    # ---- Stage 1: OFFLINE verification -- no Qdrant import may happen before
    # this fully passes (T06 requirement 4). First the upstream artifact
    # hashes (page-intermediate, canonicals, quality report) are re-verified;
    # tampered assemble outputs abort here.
    manifest = load_artifact_manifest(root_path, corpus_version, verify=True)
    if manifest is None:
        raise OfflineValidationError(
            ["artifact_manifest.json missing (assemble first)"])

    chunks, chunk_report = _collect_chunks(paths)
    total = len(chunks)
    print(f"[ingest] chunked {total} chunks across {chunk_report['documents']} docs")

    # Candidate-only chunk manifest (immutable stage chain: re-runs reuse
    # byte-identical, differing content hard-fails).
    write_chunk_manifest(root_path, corpus_version, build_chunk_manifest(chunks))
    preflight = offline_preflight(root_path, corpus_version)
    print(f"[ingest] offline preflight ok: {preflight['documents']} docs, "
          f"{preflight['total_chunks']} chunks")

    staging = staging_collection_name(corpus_version)
    report: dict[str, Any] = {
        "corpus_version": corpus_version,
        "live_collection": LIVE_COLLECTION,
        "staging_collection": staging,
        "total_chunks": total,
        "chunk_report": chunk_report,
    }

    # ---- Stage 2: build the VERSIONED STAGING collection (only after all
    # offline validation passed). A build failure is recorded in the final
    # report; live state (collection/alias/pointer) stays untouched.
    builder = _builder or _build_collection
    try:
        build_report = builder(chunks, qdrant_url, root_path,
                               collection_name=staging,
                               corpus_version=corpus_version)
    except Exception as exc:  # noqa: BLE001 -- fail-closed on any build error
        build_report = {
            "collection": staging, "qdrant_url": qdrant_url,
            "build_status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "vector_count": 0, "point_count": 0, "search_smoke": [],
        }
        report["build"] = build_report
        report["status"] = "failed"
        _write_stage_reports(root_path, corpus_version, report)
        print(f"[ingest] staging build FAILED: {build_report['error']}")
        return report

    # ---- Stage 3: the CLOSED publish gate (re-runs full offline preflight).
    gate = publish_gate(
        root_path, corpus_version,
        point_count=build_report.get("point_count", 0),
        vector_count=build_report.get("vector_count", 0),
        smoke_results=build_report.get("search_smoke") or [],
        collection_name=build_report.get("collection") or staging,
    )
    ok = gate["gate"] == "passed"
    report["build"] = build_report
    report["publish_gate"] = gate
    report["status"] = "ok" if ok else "incomplete"

    # ---- Stage 4: single in-memory report write + parent-hash chain.
    try:
        _write_stage_reports(root_path, corpus_version, report)
        print(f"[ingest] report + ingest manifest written to {art_dir}")
    except CandidateConflictError:
        # T06-R3: an already-staged version re-runs read-only. The immutable
        # chain stays exactly as first written when it verifies AND binds the
        # SAME collection identity -- the only byte difference in a reuse
        # rerun is the transient build mode ("create" -> "reuse"). Anything
        # else (a different collection, a broken chain) is still a conflict.
        manifest = load_ingest_manifest(root_path, corpus_version, verify=True)
        record = load_gate_record(root_path, corpus_version, verify=True)
        same_collection = manifest is not None and (
            manifest.get("collection_name")
            == (report.get("build") or {}).get("collection"))
        if not (same_collection and record is not None
                and record.get("gate") == "passed"):
            raise
        print("[ingest] stage chain already complete and verified -- "
              "immutable artifacts reused (report not rewritten)")

    # ---- Stage 5: gated switch -- switch_pointer re-verifies the ON-DISK
    # gate record and every hash itself; no in-memory dict is trusted. The
    # alias admin is created lazily HERE: only after all offline checks and
    # the gate passed AND publish is requested.
    if publish and ok:
        admin = _alias_admin or (
            _admin_factory(qdrant_url) if _admin_factory else None)
        switch_pointer(root_path, corpus_version, alias_admin=admin)
        print(f"[ingest] published {corpus_version}: alias/pointer switched "
              "(rollback record written)")
    elif publish:
        print("[ingest] --publish refused: publish gate did not pass "
              f"({'; '.join(gate['failures'])})")
    return report


def _write_stage_reports(
    root_path: Path, corpus_version: str, report: dict[str, Any]
) -> None:
    """Write the final ingest report ONCE and bind it via the ingest manifest.

    Both files go through the candidate-only write (absent -> atomic,
    identical -> reuse, different -> conflict); no stage rewrites them.
    """
    report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    write_candidate(root_path, corpus_version, "ingest_report.json", report_bytes)
    build = report.get("build") or {}
    ingest_manifest = build_ingest_manifest(
        root_path, corpus_version,
        chunk_manifest_sha=sha256_file(
            root_path / "data" / "canonical" / "v2" / corpus_version
            / "chunk_manifest.json"),
        ingest_report_sha=sha256_file(
            root_path / "data" / "canonical" / "v2" / corpus_version
            / "ingest_report.json"),
        # bind the CONCRETE build identity the gate verified (base staging
        # name or a fresh immutable build identity)
        collection_name=build.get("collection"),
    )
    write_ingest_manifest(root_path, corpus_version, ingest_manifest)
    if report.get("publish_gate", {}).get("gate") == "passed":
        write_gate_record(
            root_path, corpus_version,
            gate_report=report["publish_gate"],
            ingest_manifest_sha=sha256_file(
                root_path / "data" / "canonical" / "v2" / corpus_version
                / "ingest_manifest.json"))


def _print_report(report: dict[str, Any]) -> None:
    b = report["build"]
    print("=" * 72)
    print("V2 Corpus Ingestion (T06 state machine, staging + gated publish)")
    print("=" * 72)
    print(f"  corpus_version    : {report['corpus_version']}")
    print(f"  live collection   : {report['live_collection']}")
    print(f"  staging collection: {report['staging_collection']}")
    print(f"  documents         : {report['chunk_report']['documents']}")
    print(f"  total_chunks      : {report['total_chunks']}")
    print(f"  vector_count      : {b['vector_count']}")
    print(f"  point_count       : {b['point_count']}")
    print(f"  build_status      : {b['build_status']}")
    print(f"  overall_status    : {report['status']}")
    print("-" * 72)
    print("Fixed cross-document RRF smoke queries")
    print("-" * 72)
    for q in b.get("search_smoke", []):
        print(f"  q={q['query']!r}")
        print(f"    hits={q['hit_count']} top={q.get('top_file_name')} "
              f"score={q.get('top_score')} :: {q.get('top_text_head')!r}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument(
        "--corpus-version", default=None,
        help="corpus version to ingest (defaults to the published corpus-current pointer)",
    )
    parser.add_argument(
        "--publish", action="store_true",
        help="on publish-gate success, switch the live alias target and "
             "corpus-current.json to the staged collection",
    )
    parser.add_argument(
        "--root", default=None,
        help="project root override (tests only)",
    )
    args = parser.parse_args(argv)
    # T06-R3: the Qdrant admin is NOT constructed up front -- the factory
    # runs only after all offline checks and the gate passed (publish mode).
    admin_factory = QdrantAliasAdmin if args.publish else None
    report = ingest(args.qdrant_url, args.corpus_version, args.publish,
                    root=args.root, _admin_factory=admin_factory)
    _print_report(report)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
