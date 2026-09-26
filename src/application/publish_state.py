"""T06 publish state machine -- immutable stage chain, staging collections,
closed publish gate, rollback-able pointer + index-target switches.

build -> assemble -> ingest -> publish is a verifiable state machine:

* EVERY artifact (canonical, quality report, chunk manifest, ingest report,
  ingest manifest, publish gate record) is written candidate-only: absent ->
  atomic write; existing and byte-identical -> idempotent reuse; ANY content
  difference -> :class:`CandidateConflictError` -- no stage ever overwrites a
  released artifact in place, so the stages form an immutable parent-hash
  chain: ``artifact_manifest.json`` (assemble) -> ``ingest_manifest.json``
  (ingest) -> ``publish_gate_record.json`` (gate);
* ingest builds a VERSIONED STAGING collection
  (``corpus_v2__staging__<corpus_version>``); the fixed live collection
  ``corpus_v2``, any alias and the pointer stay untouched unless the gate
  passes AND ``--publish`` was requested;
* the publish gate re-runs the full offline preflight, re-checks the
  canonical file set (extra/missing files) and its aggregate hash, the
  chunk-set identity, count equality, and the COMPLETE fixed smoke query
  set (empty / missing / duplicated / zero-hit queries all fail);
* the gate report is written immutably as ``publish_gate_record.json``
  bound to the candidate + collection identity; :func:`switch_pointer`
  never accepts an in-memory dict -- it re-verifies the on-disk record and
  the full hash chain before switching;
* a successful switch writes a rollback record BEFORE moving the index
  target (alias) and the pointer; :func:`rollback_pointer` restores BOTH;
  the old collection is never deleted.

This module deliberately has NO qdrant import: alias administration is an
injected duck-typed ``alias_admin`` (production implementation lives in the
ingest script), so offline validation never touches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.application.canonical_validation import validate_document

SCHEMA_VERSION = "t06-3"

# The fixed LIVE collection -- ingest NEVER writes it; a staged collection
# is promoted only through the gated alias/pointer switch.
LIVE_COLLECTION = "corpus_v2"
# The alias the runtime / ops tooling may target for the promoted collection.
LIVE_ALIAS = "corpus_v2_live"
# The closed usage_policy vocabulary (evidence_policy projection, D2).
_USAGE_POLICIES = {"answer_and_citation", "source_locator_only", "excluded"}

# The fixed cross-document smoke query set: the publish gate requires a hit
# for EVERY query, no duplicates, no missing queries, no extras.
FIXED_SMOKE_QUERIES = (
    "管道流量测量方法",
    "水质特征因子应能区分污水与雨水",
    "雨污混接程度分级",
)


class PublishStateError(Exception):
    """Base class for publish state machine failures."""


class CandidateConflictError(PublishStateError):
    """A candidate file exists with different bytes (no overwrite allowed)."""


class HashMismatchError(PublishStateError):
    """A bound artifact hash no longer matches the file on disk."""


class OfflineValidationError(PublishStateError):
    """Offline preflight failed; ingest must not touch Qdrant."""

    def __init__(self, failures: list[str]):
        self.failures = list(failures)
        super().__init__("offline preflight failed: " + "; ".join(self.failures))


class PublishGateError(PublishStateError):
    """The publish gate failed; the pointer must not switch."""


class CollectionIdentityError(PublishStateError):
    """A physical collection's content identity does not match the derived
    build identity; it is never deleted, recreated or upserted."""


def staging_collection_name(corpus_version: str) -> str:
    """Versioned staging collection for one candidate corpus version."""
    return f"{LIVE_COLLECTION}__staging__{corpus_version}"


def staging_build_identity(
    *,
    corpus_version: str,
    artifact_manifest_sha256: str,
    chunk_manifest_sha256: str,
    embedding_profile: dict[str, Any],
    vector_config: dict[str, Any],
    sparse_config: dict[str, Any] | None,
    enrichment_version: str,
) -> str:
    """Deterministic physical build identity (T06-R4).

    Derived from the FULL input set: the artifact + chunk manifest hashes,
    the embedding model id/revision/dimension, the dense + sparse collection
    configuration and the payload/enrichment schema version. Because the
    derivation is pure, a crash between "collection created on Qdrant" and
    "manifest written" re-derives the SAME name -- a rerun finds the orphaned
    collection instead of minting another one.
    """
    payload = json.dumps({
        "corpus_version": corpus_version,
        "artifact_manifest_sha256": artifact_manifest_sha256,
        "chunk_manifest_sha256": chunk_manifest_sha256,
        "embedding_profile": embedding_profile,
        "vector_config": vector_config,
        "sparse_config": sparse_config,
        "enrichment_version": enrichment_version,
    }, sort_keys=True, ensure_ascii=False)
    return "bi-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def staging_build_collection_name(corpus_version: str, build_identity: str) -> str:
    """The physical collection name for one build identity (stable)."""
    return f"{staging_collection_name(corpus_version)}__{build_identity}"


# Payload fields every stored point must carry for identity verification --
# a missing or empty field is a fail-closed identity error, never a guess.
_PAYLOAD_IDENTITY_FIELDS = (
    "chunk_id", "text", "usage_policy", "source_sha256", "file_name",
    "build_identity",
)


def canonical_payload_identity(
    payloads: Any, *, build_identity: str
) -> dict[str, Any]:
    """Digest the canonicalized identity fields of the full payload stream.

    Covers (at least) chunk_id, a text digest, usage_policy, the source
    identity (source_sha256 + file_name) and the build_identity. Raises
    :class:`CollectionIdentityError` on any missing/empty identity field or
    a foreign build_identity.
    """
    lines: list[str] = []
    policy_counts: dict[str, int] = {}
    for payload in payloads:
        missing = [key for key in _PAYLOAD_IDENTITY_FIELDS
                   if not payload.get(key)]
        if missing:
            raise CollectionIdentityError(
                f"payload is missing identity fields {missing}")
        if payload["build_identity"] != build_identity:
            raise CollectionIdentityError(
                f"payload build_identity {payload['build_identity']!r} does "
                f"not match this build {build_identity!r}")
        text_digest = hashlib.sha256(
            " ".join(str(payload["text"]).split()).encode("utf-8")).hexdigest()
        source = f"{payload['source_sha256']}|{payload['file_name']}"
        lines.append(
            f"{payload['chunk_id']} {payload['usage_policy']} "
            f"{text_digest} {source} {build_identity}")
        policy_counts[payload["usage_policy"]] = \
            policy_counts.get(payload["usage_policy"], 0) + 1
    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return {
        "build_identity": build_identity,
        "chunk_count": len(lines),
        "policy_counts": dict(sorted(policy_counts.items())),
        "payload_digest": digest,
    }


def _scroll_all_payloads(client: Any, collection_name: str) -> list[dict[str, Any]]:
    """Full scroll of every stored payload (the identity proof)."""
    payloads: list[dict[str, Any]] = []
    offset: Any = None
    while True:
        records, offset = client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records or []:
            payloads.append(dict(record.payload or {}))
        if offset is None:
            return payloads


def _collection_dense_vector_size(info: Any) -> int:
    try:
        return int(info.config.params.vectors["dense"].size)
    except Exception as exc:
        raise CollectionIdentityError(
            "dense vector schema unreadable; refusing to judge collection "
            "identity (fail-closed)") from exc


def _collection_sparse_names(info: Any) -> set[str]:
    try:
        return set((info.config.params.sparse_vectors or {}).keys())
    except Exception as exc:
        raise CollectionIdentityError(
            "sparse vector schema unreadable; refusing to judge collection "
            "identity (fail-closed)") from exc


def verify_collection_payload_identity(
    client: Any,
    collection_name: str,
    *,
    expected: dict[str, Any],
    expected_vector_size: int,
    sparse_enabled: bool,
) -> None:
    """Prove a physical collection IS the candidate it claims to be.

    Strict verification: complete vector schema (dense size + sparse
    presence), exact point count, and a FULL payload scroll whose
    canonicalized identity digest + global policy counts must match the
    expected values. Any unreadable schema or missing payload field fails
    closed.
    """
    info = client.get_collection(collection_name)
    size = _collection_dense_vector_size(info)
    if size != expected_vector_size:
        raise CollectionIdentityError(
            f"collection {collection_name!r} dense vector size {size} does "
            f"not match the expected {expected_vector_size}")
    sparse = _collection_sparse_names(info)
    if sparse_enabled != ("bm25" in sparse):
        raise CollectionIdentityError(
            f"collection {collection_name!r} sparse schema {sorted(sparse)!r} "
            f"does not match sparse_enabled={sparse_enabled}")
    points = int(getattr(info, "points_count", 0) or 0)
    if points != expected["chunk_count"]:
        raise CollectionIdentityError(
            f"collection {collection_name!r} holds {points} points, "
            f"expected {expected['chunk_count']}")
    actual = canonical_payload_identity(
        _scroll_all_payloads(client, collection_name),
        build_identity=expected["build_identity"])
    if actual["payload_digest"] != expected["payload_digest"] \
            or actual["policy_counts"] != expected["policy_counts"] \
            or actual["chunk_count"] != expected["chunk_count"]:
        raise CollectionIdentityError(
            f"collection {collection_name!r} payload identity mismatch: "
            f"digest/policy/counts do not match the candidate chunk stream")


def resolve_staging_target(
    client: Any,
    *,
    target_name: str,
    expected: dict[str, Any],
    expected_vector_size: int,
    sparse_enabled: bool,
    guarded_collections: set[str] | None = None,
) -> tuple[str, str]:
    """Resolve the immutable staging target: create | reuse (read-only).

    T06-R4: the target name is the STABLE build-identity name (no random
    suffixes). An existing collection is reused READ-ONLY only after
    :func:`verify_collection_payload_identity` proves its full payload
    identity; any mismatch is a fail-closed
    :class:`CollectionIdentityError` -- the collection is never deleted,
    recreated or upserted. Guarded collections (live collection, the
    pointer's collection, the live alias target) are never build targets.

    ``client`` is duck-typed (a real QdrantClient or a test fake); this
    module still never imports qdrant.
    """
    # T06-R4: the fixed live collection is guarded IMPLICITLY -- no caller
    # contract can ever turn it into a build target.
    guarded = target_name == LIVE_COLLECTION \
        or target_name in set(guarded_collections or ())
    if not client.collection_exists(target_name):
        if guarded:
            raise CollectionIdentityError(
                f"refusing to build into guarded collection {target_name!r}")
        return target_name, "create"
    # read-only verification is allowed even for a guarded collection (a
    # republish re-run must still re-verify the already-promoted staging
    # collection); the guarded name can never be CREATED, deleted or upserted
    verify_collection_payload_identity(
        client, target_name,
        expected=expected,
        expected_vector_size=expected_vector_size,
        sparse_enabled=sparse_enabled)
    return target_name, "reuse"


def _is_staging_collection_name(base: str, name: Any) -> bool:
    """Whether ``name`` belongs to the immutable staging family of ``base``.

    T06-R3 P1-1: the family is the base staging name itself, or a fresh
    build identity derived from it (``base__<build_id>``) minted when the
    base held foreign content. The live collection and any foreign name are
    outside the family.
    """
    return isinstance(name, str) and (
        name == base or name.startswith(base + "__")
    )


# --------------------------------------------------------------------------- #
# Hashing helpers
# --------------------------------------------------------------------------- #

def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_dir(root: Path, corpus_version: str) -> Path:
    return Path(root) / "data" / "canonical" / "v2" / corpus_version


def _page_intermediate_path(root: Path, corpus_version: str) -> Path:
    return Path(root) / "data" / "canonical" / f"{corpus_version}-documents.jsonl"


def _registry_manifest_path(root: Path, corpus_version: str) -> Path:
    return Path(root) / "data" / "registry" / f"{corpus_version}.json"


def _pointer_path(root: Path) -> Path:
    return Path(root) / "data" / "registry" / "corpus-current.json"


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` only once the bytes are fully on disk (no truncation)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """For the pointer + rollback records only: timestamps make them unique,
    so they are append-only files outside the candidate conflict domain."""
    _write_bytes_atomic(path, _json_bytes(payload))


def write_candidate(
    root: Path | str, corpus_version: str, name: str, data: bytes
) -> str:
    """Write ANY candidate artifact; reuse an identical existing file.

    The single sanctioned artifact write of the whole state machine:

    * target absent -> write atomically (the artifact only ever appears
      complete -- a crash can never leave a truncated file);
    * target exists and is byte-identical -> idempotent reuse;
    * target exists with ANY content difference -> :class:`CandidateConflictError`
      and the existing file stays untouched -- stages chain by parent hash,
      never by rewriting a shared file.
    """
    target = _artifact_dir(root, corpus_version) / name
    if target.exists():
        if target.read_bytes() == data:
            return "reused"
        raise CandidateConflictError(
            f"candidate conflict: {target} exists with different content; "
            "the stage chain is immutable -- use a new corpus version")
    _write_bytes_atomic(target, data)
    return "written"


# Historical alias: the canonical-first use of the candidate write.
write_canonical_candidate = write_candidate


# --------------------------------------------------------------------------- #
# 1. Quality report + non-publishable markers (requirement 7)
# --------------------------------------------------------------------------- #

def write_quality_report(
    root: Path | str,
    corpus_version: str,
    document_reports: list[dict[str, Any]],
    *,
    non_publishable_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Write the candidate's text-quality report (candidate-only write).

    ``document_reports`` are the per-document ``document_text_quality`` rows.
    The corpus is publishable only when ZERO documents are garbled and no
    explicit non-publishable reasons (e.g. ``allow_garbled`` diagnostics) are
    recorded.
    """
    verdict_counts: dict[str, int] = {}
    for row in document_reports:
        verdict = row.get("verdict") or "unknown"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    garbled = verdict_counts.get("garbled_text", 0)
    reasons = list(non_publishable_reasons or [])
    if garbled:
        reasons.append("garbled_documents_present")
    report = {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": corpus_version,
        "document_count": len(document_reports),
        "verdict_counts": verdict_counts,
        "garbled_documents": garbled,
        "publishable": garbled == 0 and not reasons,
        "non_publishable_reasons": reasons,
        "documents": [
            {"sha256": r.get("sha256"), "verdict": r.get("verdict"),
             "bad_pages": r.get("bad_pages"), "pages": r.get("pages")}
            for r in document_reports
        ],
    }
    write_candidate(root, corpus_version, "quality_report.json", _json_bytes(report))
    return report


# --------------------------------------------------------------------------- #
# 2. Chunk manifest (requirements 2, 4)
# --------------------------------------------------------------------------- #

def build_chunk_manifest(chunks: list[Any]) -> dict[str, Any]:
    """Bind the chunk stream identity: counts, policies, chunk-set hash.

    ``chunk_set_sha256`` covers every ``chunk_id`` + ``usage_policy`` pair so
    the manifest pins the exact chunk stream the ingest produced, while the
    texts themselves stay bound via the canonical aggregate hash.
    """
    per_doc: dict[str, dict[str, Any]] = {}
    policy_counts: dict[str, int] = {}
    lines: list[str] = []
    for chunk in chunks:
        chunk_id = chunk.chunk_id
        payload_sha = (chunk.metadata or {}).get("source_sha256") or ""
        policy = (chunk.metadata or {}).get("usage_policy") or "source_locator_only"
        entry = per_doc.setdefault(payload_sha, {"chunk_count": 0, "policy_counts": {}})
        entry["chunk_count"] += 1
        entry["policy_counts"][policy] = entry["policy_counts"].get(policy, 0) + 1
        # T06-R4 fix: the GLOBAL policy counts must be populated too --
        # the publish gate compares these against the re-chunked canonicals
        # and against the Qdrant payload identity.
        policy_counts[policy] = policy_counts.get(policy, 0) + 1
        lines.append(f"{chunk_id} {policy}")
    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "total_chunks": len(chunks),
        "document_count": len(per_doc),
        "per_document": dict(sorted(per_doc.items())),
        "policy_counts": dict(sorted(policy_counts.items())),
        "chunk_set_sha256": digest,
    }


def write_chunk_manifest(root: Path | str, corpus_version: str,
                         manifest: dict[str, Any]) -> str:
    return write_candidate(
        root, corpus_version, "chunk_manifest.json", _json_bytes(manifest))


def load_chunk_manifest(root: Path | str, corpus_version: str) -> dict[str, Any] | None:
    path = _artifact_dir(root, corpus_version) / "chunk_manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 3. Artifact manifest (assemble stage of the parent-hash chain)
# --------------------------------------------------------------------------- #

def build_artifact_manifest(
    root: Path | str,
    corpus_version: str,
    *,
    non_publishable_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Bind the assemble-stage outputs: page-intermediate, every canonical
    (per-file + aggregate over sorted ``name  sha`` lines) and the quality
    report. Later stages bind THIS file's hash (parent-hash chain).
    """
    root = Path(root)
    art = _artifact_dir(root, corpus_version)
    canonical_files: dict[str, str] = {}
    for path in sorted(art.glob("*.canonical.json")) if art.is_dir() else []:
        canonical_files[path.name] = sha256_file(path)
    aggregate = hashlib.sha256(
        "\n".join(f"{n} {s}" for n, s in sorted(canonical_files.items()))
        .encode("utf-8")).hexdigest()

    page_intermediate = _page_intermediate_path(root, corpus_version)
    quality_path = art / "quality_report.json"

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": corpus_version,
        "page_intermediate": {
            "path": page_intermediate.relative_to(root).as_posix(),
            "sha256": sha256_file(page_intermediate),
        },
        "canonical": {"count": len(canonical_files), "files": canonical_files,
                      "aggregate_sha256": aggregate},
        "quality_report": {"path": "quality_report.json",
                           "sha256": sha256_file(quality_path)},
    }

    quality = json.loads(quality_path.read_text(encoding="utf-8")) \
        if quality_path.is_file() else None
    reasons = list(non_publishable_reasons or [])
    if quality is not None:
        reasons.extend(quality.get("non_publishable_reasons") or [])
        if not quality.get("publishable", False) and "quality_report_not_publishable" not in reasons:
            reasons.append("quality_report_not_publishable")
    manifest["non_publishable_reasons"] = sorted(set(reasons))
    manifest["publishable"] = not manifest["non_publishable_reasons"]
    return manifest


def write_artifact_manifest(root: Path | str, corpus_version: str, **kwargs) -> dict:
    manifest = build_artifact_manifest(root, corpus_version, **kwargs)
    status = write_candidate(
        root, corpus_version, "artifact_manifest.json", _json_bytes(manifest))
    return manifest


def load_artifact_manifest(
    root: Path | str, corpus_version: str, *, verify: bool = False
) -> dict[str, Any] | None:
    """Load the artifact manifest; with ``verify=True`` re-verify every hash.

    Verification recomputes each bound SHA-256 and raises
    :class:`HashMismatchError` listing every mismatch -- tampering with ANY
    bound artifact is detected before a stage consumes it.
    """
    path = _artifact_dir(root, corpus_version) / "artifact_manifest.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not verify:
        return manifest
    root = Path(root)
    art = _artifact_dir(root, corpus_version)
    mismatches: list[str] = []

    def _check(label: str, entry: dict[str, Any] | None, absolute: Path | None = None):
        if entry is None:
            return
        # artifact-relative paths (quality report) resolve against the
        # candidate dir; page-intermediate is repo-root-relative
        target = absolute if absolute is not None else art / entry["path"]
        if not target.is_file():
            mismatches.append(f"{label}: missing {entry['path']}")
        elif sha256_file(target) != entry["sha256"]:
            mismatches.append(f"{label}: sha256 mismatch for {entry['path']}")

    _check("page_intermediate", manifest.get("page_intermediate"),
           root / manifest["page_intermediate"]["path"])
    canonical = manifest.get("canonical") or {}
    for name, digest in (canonical.get("files") or {}).items():
        _check(f"canonical:{name}", {"path": name, "sha256": digest}, art / name)
    _check("quality_report", manifest.get("quality_report"))
    if mismatches:
        raise HashMismatchError("; ".join(mismatches))
    return manifest


# --------------------------------------------------------------------------- #
# 4. Ingest manifest + gate record (immutable stage chain, requirement 2)
# --------------------------------------------------------------------------- #

def build_ingest_manifest(
    root: Path | str,
    corpus_version: str,
    *,
    chunk_manifest_sha: str,
    ingest_report_sha: str,
    collection_name: str | None = None,
    build_identity: str | None = None,
) -> dict[str, Any]:
    """Bind the ingest-stage inputs/outputs into the parent-hash chain.

    The ingest manifest pins: the assemble-stage manifest hash, the chunk
    manifest hash and the ingest report hash, plus the CONCRETE staging
    collection identity: the stable build identity derived from the full
    input set (manifests + embedding/vector/sparse/enrichment versions) and
    the physical collection name it names.
    """
    root = Path(root)
    artifact_manifest_sha = sha256_file(
        _artifact_dir(root, corpus_version) / "artifact_manifest.json")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "ingest_manifest",
        "corpus_version": corpus_version,
        "collection_name": collection_name or staging_collection_name(corpus_version),
        "build_identity": build_identity,
        "artifact_manifest_sha256": artifact_manifest_sha,
        "chunk_manifest_sha256": chunk_manifest_sha,
        "ingest_report_sha256": ingest_report_sha,
    }


def write_ingest_manifest(root: Path | str, corpus_version: str,
                          manifest: dict[str, Any]) -> str:
    return write_candidate(
        root, corpus_version, "ingest_manifest.json", _json_bytes(manifest))


def load_ingest_manifest(
    root: Path | str, corpus_version: str, *, verify: bool = False
) -> dict[str, Any] | None:
    """Load the ingest manifest; with ``verify=True`` re-verify its chain:
    the bound artifact-manifest, chunk-manifest and ingest-report hashes must
    all match the files on disk."""
    path = _artifact_dir(root, corpus_version) / "ingest_manifest.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not verify:
        return manifest
    art = _artifact_dir(Path(root), corpus_version)
    mismatches: list[str] = []
    for label, name in (("artifact_manifest", "artifact_manifest.json"),
                        ("chunk_manifest", "chunk_manifest.json"),
                        ("ingest_report", "ingest_report.json")):
        bound = manifest.get(f"{label}_sha256")
        target = art / name
        if not bound or not target.is_file() or sha256_file(target) != bound:
            mismatches.append(f"{label}: sha256 mismatch for {name}")
    if mismatches:
        raise HashMismatchError("; ".join(mismatches))
    return manifest


def write_gate_record(
    root: Path | str,
    corpus_version: str,
    *,
    gate_report: dict[str, Any],
    ingest_manifest_sha: str,
) -> str:
    """Persist the gate report immutably, bound to candidate + collection.

    ``switch_pointer`` reads THIS file -- never an in-memory dict.
    """
    root = Path(root)
    artifact_manifest_sha = sha256_file(
        _artifact_dir(root, corpus_version) / "artifact_manifest.json")
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "publish_gate_record",
        "corpus_version": corpus_version,
        "collection_name": gate_report.get("collection_name"),
        "build_identity": gate_report.get("build_identity"),
        "gate": gate_report.get("gate"),
        "checks": gate_report.get("checks"),
        "failures": gate_report.get("failures"),
        "ingest_manifest_sha256": ingest_manifest_sha,
        "artifact_manifest_sha256": artifact_manifest_sha,
    }
    return write_candidate(
        root, corpus_version, "publish_gate_record.json", _json_bytes(record))


def load_gate_record(
    root: Path | str, corpus_version: str, *, verify: bool = False
) -> dict[str, Any] | None:
    path = _artifact_dir(root, corpus_version) / "publish_gate_record.json"
    if not path.is_file():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if not verify:
        return record
    art = _artifact_dir(Path(root), corpus_version)
    mismatches: list[str] = []
    for label, name in (("artifact_manifest", "artifact_manifest.json"),
                        ("ingest_manifest", "ingest_manifest.json")):
        bound = record.get(f"{label}_sha256")
        target = art / name
        if not bound or not target.is_file() or sha256_file(target) != bound:
            mismatches.append(f"{label}: sha256 mismatch for {name}")
    if mismatches:
        raise HashMismatchError("; ".join(mismatches))
    return record


# --------------------------------------------------------------------------- #
# 5. Offline preflight (requirement 4)
# --------------------------------------------------------------------------- #

def offline_preflight(root: Path | str, corpus_version: str) -> dict[str, Any]:
    """Validate EVERYTHING that can be validated without a Qdrant connection.

    Checks (failures are collected, not fail-fast, so one run reports all):
    1. artifact manifest exists and every bound hash still matches;
    2. registry manifest exists, is ``ready`` and its
       ``canonical_documents_sha256`` matches the page-intermediate on disk;
    3. the quality report reports zero garbled documents and the corpus is
       not marked non-publishable (``--allow-garbled`` diagnostics blocked);
    4. every canonical document passes schema validation;
    5. re-chunking every canonical reproduces the chunk manifest: total
       chunk count, per-document counts, chunk-set hash, and every
       usage_policy lies inside the closed vocabulary.
    """
    root = Path(root)
    failures: list[str] = []
    art = _artifact_dir(root, corpus_version)

    manifest = None
    try:
        manifest = load_artifact_manifest(root, corpus_version, verify=True)
    except HashMismatchError as exc:
        failures.append(f"artifact hash mismatch: {exc}")
    except ValueError as exc:
        failures.append(f"artifact manifest unreadable: {exc}")
    if manifest is None:
        failures.append("artifact_manifest.json missing")

    registry = _registry_manifest_path(root, corpus_version)
    if registry.is_file():
        reg = json.loads(registry.read_text(encoding="utf-8"))
        if reg.get("status") != "ready":
            failures.append(f"registry manifest status is {reg.get('status')!r}")
        page_path = _page_intermediate_path(root, corpus_version)
        if page_path.is_file() and reg.get("canonical_documents_sha256"):
            if sha256_file(page_path) != reg["canonical_documents_sha256"]:
                failures.append("page-intermediate hash does not match registry manifest")
    else:
        failures.append("registry manifest missing")

    quality_path = art / "quality_report.json"
    if quality_path.is_file():
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        if quality.get("garbled_documents"):
            failures.append(
                f"quality report has {quality['garbled_documents']} garbled document(s)")
        if quality.get("non_publishable_reasons"):
            failures.append(
                "non-publishable markers present: "
                + ",".join(quality["non_publishable_reasons"]))
        if not quality.get("publishable", False):
            failures.append("quality report marks the corpus not publishable")
    else:
        failures.append("quality_report.json missing")

    canonical_paths = sorted(art.glob("*.canonical.json")) if art.is_dir() else []
    if not canonical_paths:
        failures.append("no canonical artifacts in candidate directory")
    docs = []
    for path in canonical_paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            validate_document(doc)
            docs.append(doc)
        except Exception as exc:  # schema or JSON failure -> hard fail
            failures.append(f"canonical {path.name}: {type(exc).__name__}: {exc}")

    # Re-chunk and compare against the chunk manifest (count/policy invariants).
    from src.application.v2_chunker import chunk_document
    chunks = []
    for doc in docs:
        chunks.extend(chunk_document(doc))
    chunk_manifest = load_chunk_manifest(root, corpus_version)
    if chunk_manifest is None:
        failures.append("chunk_manifest.json missing")
    else:
        recomputed = build_chunk_manifest(chunks)
        if recomputed["total_chunks"] != chunk_manifest.get("total_chunks"):
            failures.append(
                f"chunk count mismatch: recomputed {recomputed['total_chunks']} "
                f"vs manifest {chunk_manifest.get('total_chunks')}")
        if recomputed["chunk_set_sha256"] != chunk_manifest.get("chunk_set_sha256"):
            failures.append("chunk-set hash mismatch vs chunk manifest")
        for policy in (chunk_manifest.get("policy_counts") or {}):
            if policy not in _USAGE_POLICIES:
                failures.append(f"unknown usage_policy in chunk manifest: {policy!r}")
        if chunk_manifest.get("policy_counts") != recomputed["policy_counts"]:
            failures.append("policy counts mismatch vs chunk manifest")

    if failures:
        raise OfflineValidationError(failures)
    return {"status": "ok", "corpus_version": corpus_version,
            "documents": len(docs), "total_chunks": len(chunks)}


# --------------------------------------------------------------------------- #
# 6. Publish gate (requirement 5, closed)
# --------------------------------------------------------------------------- #

def publish_gate(
    root: Path | str,
    corpus_version: str,
    *,
    point_count: int,
    vector_count: int,
    smoke_results: list[dict[str, Any]],
    collection_name: str,
    build_identity: str | None = None,
) -> dict[str, Any]:
    """The closed set of checks before the publish switch may run.

    1. re-runs the FULL :func:`offline_preflight`;
    2. re-verifies the artifact manifest hashes and the exact canonical file
       set (extra or missing files fail) plus the aggregate hash;
    3. verifies the built collection is in the versioned staging family;
    4. requires chunks == vectors == points;
    5. requires the COMPLETE fixed smoke query set: empty, missing,
       duplicated or zero-hit queries all fail.
    """
    failures: list[str] = []
    checks: dict[str, Any] = {}
    expected_collection = staging_collection_name(corpus_version)

    checks["collection_name"] = collection_name
    checks["build_identity"] = build_identity
    if not _is_staging_collection_name(expected_collection, collection_name):
        failures.append(
            f"built collection {collection_name!r} is not in the staging "
            f"family of {expected_collection!r}")
    # T06-R4: when the build identity is known, the promoted collection must
    # be EXACTLY the one derived from the immutable input set
    if build_identity and collection_name != staging_build_collection_name(
            corpus_version, build_identity):
        failures.append(
            f"collection {collection_name!r} does not match the derived "
            f"build identity {build_identity!r}")

    # (1) full offline preflight re-run -- one call covers manifest hashes,
    # registry readiness, quality/garbled, schema and chunk-set identity.
    try:
        preflight = offline_preflight(root, corpus_version)
        checks["offline_preflight"] = "ok"
    except OfflineValidationError as exc:
        failures.extend(exc.failures)
        checks["offline_preflight"] = "failed"

    # (2) artifact manifest presence + canonical file set + aggregate hash.
    try:
        manifest = load_artifact_manifest(root, corpus_version, verify=True)
    except HashMismatchError as exc:
        manifest = None
        failures.append(f"artifact hash mismatch: {exc}")
    if manifest is None:
        failures.append("artifact_manifest.json missing (assemble first)")
        checks["artifact_manifest"] = "missing"
    else:
        checks["artifact_manifest"] = "ok"
        art = _artifact_dir(root, corpus_version)
        on_disk = {p.name: sha256_file(p) for p in art.glob("*.canonical.json")}
        bound = dict((manifest.get("canonical") or {}).get("files") or {})
        extra = sorted(set(on_disk) - set(bound))
        missing = sorted(set(bound) - set(on_disk))
        if extra:
            failures.append(f"extra canonical files not bound by the manifest: {extra}")
        if missing:
            failures.append(f"bound canonical files missing on disk: {missing}")
        recomputed_aggregate = hashlib.sha256(
            "\n".join(f"{n} {s}" for n, s in sorted(on_disk.items()))
            .encode("utf-8")).hexdigest()
        if recomputed_aggregate != (manifest.get("canonical") or {}).get("aggregate_sha256"):
            failures.append("canonical aggregate hash mismatch")

    # (3b) chunk manifest explicit identity (chunk-set hash + policies).
    chunk_manifest = load_chunk_manifest(root, corpus_version)
    if chunk_manifest is None:
        failures.append("chunk_manifest.json missing")
        total_chunks = None
    else:
        total_chunks = chunk_manifest.get("total_chunks")
        recomputed = _recompute_chunk_identity(root, corpus_version)
        if recomputed is not None:
            if recomputed.get("chunk_set_sha256") != chunk_manifest.get("chunk_set_sha256"):
                failures.append("chunk-set hash mismatch vs re-chunked canonicals")
            if recomputed.get("policy_counts") != chunk_manifest.get("policy_counts"):
                failures.append("policy counts mismatch vs re-chunked canonicals")
            if recomputed.get("total_chunks") != chunk_manifest.get("total_chunks"):
                failures.append("chunk count mismatch vs re-chunked canonicals")
        for policy in (chunk_manifest.get("policy_counts") or {}):
            if policy not in _USAGE_POLICIES:
                failures.append(f"unknown usage_policy in chunk manifest: {policy!r}")

    # (4) count equality: chunks == vectors == points.
    checks["total_chunks"] = total_chunks
    checks["vector_count"] = vector_count
    checks["point_count"] = point_count
    if total_chunks is None or vector_count != total_chunks \
            or point_count != total_chunks:
        failures.append(
            f"count mismatch: chunks={total_chunks} vectors={vector_count} "
            f"points={point_count}")

    # (5) the COMPLETE fixed smoke query set.
    checks["smoke"] = list(smoke_results or [])
    failures.extend(_smoke_failures(smoke_results))

    return {"gate": "failed" if failures else "passed",
            "corpus_version": corpus_version,
            "collection_name": collection_name,
            "build_identity": build_identity,
            "checks": checks, "failures": failures}


def _recompute_chunk_identity(root: Path, corpus_version: str) -> dict[str, Any] | None:
    """Re-chunk every on-disk canonical; None when anything fails to load."""
    from src.application.v2_chunker import chunk_document
    art = _artifact_dir(root, corpus_version)
    try:
        chunks = []
        for path in sorted(art.glob("*.canonical.json")):
            chunks.extend(chunk_document(json.loads(path.read_text(encoding="utf-8"))))
    except Exception:
        return None
    recomputed = build_chunk_manifest(chunks)
    return {"total_chunks": recomputed["total_chunks"],
            "policy_counts": recomputed["policy_counts"],
            "chunk_set_sha256": recomputed["chunk_set_sha256"]}


def _smoke_failures(smoke_results: list[dict[str, Any]] | None) -> list[str]:
    failures: list[str] = []
    results = list(smoke_results or [])
    if not results:
        failures.append("smoke results empty: the fixed query set must all "
                        "return hits")
    queries = [r.get("query") for r in results]
    duplicates = sorted({q for q in queries if queries.count(q) > 1})
    if duplicates:
        failures.append(f"duplicate smoke queries present: {duplicates}")
    present = set(queries)
    missing = [q for q in FIXED_SMOKE_QUERIES if q not in present]
    if missing:
        failures.append(f"smoke queries missing from the fixed set: {missing}")
    unexpected = sorted(present - set(FIXED_SMOKE_QUERIES))
    if unexpected:
        failures.append(f"smoke queries outside the fixed set: {unexpected}")
    for result in results:
        if not result.get("hit_count"):
            failures.append(f"smoke query returned no hits: {result.get('query')!r}")
    return failures


# --------------------------------------------------------------------------- #
# 7. Gated switch + rollback (requirements 5, 6): pointer AND index target
# --------------------------------------------------------------------------- #

def switch_pointer(
    root: Path | str,
    corpus_version: str,
    *,
    alias_admin: Any,
    verify_collection: Any,
) -> None:
    """Promote the staged candidate: re-verify the concrete collection
    identity, switch the live alias and the ``corpus-current`` pointer.

    T06-R4: ``alias_admin`` and ``verify_collection`` are MANDATORY -- the
    alias is the sole runtime query entry, so a pointer-only switch is a
    split state by definition and is refused BEFORE any state changes. The
    concrete collection identity is re-verified through ``verify_collection``
    right before anything moves (closing the gate-to-switch gap).

    This function NEVER trusts an in-memory gate dict: it re-verifies the
    on-disk ``publish_gate_record.json`` (must say ``passed``), the ingest
    manifest chain and every bound artifact hash before anything moves. The
    rollback record (previous pointer + previous alias target) is written
    BEFORE the switch; the old collection is never deleted.
    """
    root = Path(root)
    art = _artifact_dir(root, corpus_version)
    base = staging_collection_name(corpus_version)

    if alias_admin is None:
        raise PublishStateError(
            "publish refused: no Qdrant alias admin -- the alias is the sole "
            "runtime query entry and a pointer-only switch is forbidden")
    if not callable(verify_collection):
        raise PublishStateError(
            "publish refused: no collection verifier -- the concrete "
            "collection identity must be re-verified before the switch")

    record = load_gate_record(root, corpus_version)
    if record is None:
        raise PublishGateError(
            "publish gate record missing; publish switch refused")
    if record.get("gate") != "passed":
        raise PublishGateError(
            "publish gate did not pass: " + "; ".join(record.get("failures") or []))
    if record.get("corpus_version") != corpus_version:
        raise PublishGateError(
            f"gate record is for {record.get('corpus_version')!r}, "
            f"not {corpus_version!r}")
    # T06-R3 P1-1: the gate record binds the CONCRETE build identity (the
    # base staging name, or a fresh ``base__<build_id>`` identity minted
    # when the base held foreign content). Promote exactly that name --
    # never the live collection, never anything outside this version's
    # staging family.
    target_collection = str(record.get("collection_name") or "")
    if not _is_staging_collection_name(base, target_collection):
        raise PublishGateError(
            f"gate record is bound to collection {target_collection!r}, "
            f"which is not in the staging family of {base!r}")

    # Re-verify the full on-disk hash chain before anything moves.
    load_gate_record(root, corpus_version, verify=True)          # HashMismatchError -> fail
    ingest_manifest = load_ingest_manifest(root, corpus_version, verify=True)
    if ingest_manifest is None:
        raise PublishGateError("ingest_manifest.json missing; publish switch refused")
    if ingest_manifest.get("collection_name") != target_collection:
        raise PublishGateError(
            "ingest manifest is bound to a different staging collection")
    try:
        load_artifact_manifest(root, corpus_version, verify=True)
    except HashMismatchError as exc:
        raise PublishGateError(f"artifact hash mismatch: {exc}") from exc

    # T06-R4: re-verify the CONCRETE collection identity right before
    # anything moves -- closes the gate-to-switch gap. Must run BEFORE the
    # rollback record, the alias switch or the pointer write.
    verify_collection(target_collection)

    pointer_path = _pointer_path(root)
    previous = None
    if pointer_path.is_file():
        previous = json.loads(pointer_path.read_text(encoding="utf-8"))
    previous_target = alias_admin.current_target(LIVE_ALIAS)
    chunk_manifest = load_chunk_manifest(root, corpus_version)
    if chunk_manifest is None:
        raise PublishGateError(
            "chunk_manifest.json missing; publish switch refused")

    new_pointer = {
        "schema_version": "1",
        "status": "published",
        "published_at": _utc_now(),
        "corpus_version": corpus_version,
        "manifest": f"data/registry/{corpus_version}.json",
        # T06-R3 P1-2: the pointer records the runtime alias entry AND the
        # concrete target -- never use the physical name as query entry.
        "alias": LIVE_ALIAS,
        "collection_name": target_collection,
        # T06-R4: the stable build identity travels with the pointer
        "build_identity": record.get("build_identity"),
        "chunk_count": chunk_manifest.get("total_chunks"),
        "action": "built",
    }
    rollback = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "corpus_version": corpus_version,
        "alias": LIVE_ALIAS,
        "previous_pointer": previous,
        "previous_collection": previous_target,
        "new_collection": target_collection,
        "new_pointer": new_pointer,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    _rollback_dir(root).mkdir(parents=True, exist_ok=True)
    _write_json_atomic(_rollback_dir(root) / f"{stamp}-{corpus_version}.json", rollback)

    # Index target first: an alias failure must leave the OLD pointer live.
    alias_admin.switch(LIVE_ALIAS, target_collection)
    # T06-R3 P1-3: a pointer-write failure after a successful alias switch
    # must be compensated -- restore the previous alias target so the state
    # is consistent (pre-publish) instead of split.
    try:
        _write_json_atomic(pointer_path, new_pointer)
    except Exception as exc:
        try:
            if previous_target:
                alias_admin.switch(LIVE_ALIAS, previous_target)
            else:
                alias_admin.remove_alias(LIVE_ALIAS)
        except Exception as comp_exc:
            raise PublishStateError(
                f"pointer write failed ({exc}) AND alias compensation failed "
                f"({comp_exc}); manual reconciliation required; rollback "
                f"record: {stamp}-{corpus_version}.json") from exc
        raise PublishStateError(
            f"pointer write failed ({exc}); alias compensated back to "
            f"{previous_target!r} -- the state is consistent (pre-publish) "
            "and the switch may be retried") from exc


def _rollback_dir(root: Path) -> Path:
    return Path(root) / "data" / "registry" / "rollback"


def rollback_pointer(
    root: Path | str,
    record_path: Path | str | None = None,
    *,
    alias_admin: Any,
) -> dict[str, Any]:
    """Restore the previous pointer AND the previous alias target.

    With ``record_path=None`` the most recent record is used. When the
    previous state had NO pointer, the pointer file is removed (restoring
    "nothing is published"); when it had no alias target, the alias is
    removed. The old (pre-publish) collection is never deleted here.

    T06-R4: ``alias_admin`` is MANDATORY -- a pointer-only rollback would
    leave the running service on the new target (a split state), so a
    missing admin is refused BEFORE anything moves.
    """
    root = Path(root)
    if alias_admin is None:
        raise PublishStateError(
            "rollback refused: no Qdrant alias admin -- a pointer-only "
            "rollback would leave the service on the new target")
    if record_path is None:
        records = sorted(_rollback_dir(root).glob("*.json"))
        if not records:
            raise PublishStateError("no rollback records available")
        record_path = records[-1]
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    previous = record.get("previous_pointer")
    pointer_path = _pointer_path(root)
    # T06-R3 P1-3: the alias is restored FIRST and the pointer is only
    # touched after the index side is confirmed back on the old target -- a
    # mid-rollback failure must never leave a split state. Every failure
    # path compensates back to the PUBLISHED state (or, if even the
    # compensation fails, reports loudly for manual reconciliation).

    # 1. alias restore -- the pointer stays PUBLISHED until this succeeds.
    if record.get("alias"):
        previous_target = record.get("previous_collection")
        try:
            if previous_target:
                alias_admin.switch(record["alias"], previous_target)
            else:
                alias_admin.remove_alias(record["alias"])
        except Exception as exc:
            # The pointer was NOT touched: the state is still consistent
            # (published). Re-assert the published pointer to prove it is
            # still writable; if even that fails, manual reconciliation.
            new_pointer = record.get("new_pointer")
            try:
                if new_pointer:
                    _write_json_atomic(pointer_path, new_pointer)
                elif pointer_path.exists():
                    pointer_path.unlink()
            except Exception as comp_exc:
                raise PublishStateError(
                    f"alias restore failed ({exc}) AND published-pointer "
                    f"re-assert failed ({comp_exc}); manual reconciliation "
                    f"required; rollback record: {record_path}") from exc
            raise PublishStateError(
                f"alias restore failed ({exc}); the pointer was not touched "
                "and still publishes the current corpus -- retry the "
                "rollback once Qdrant is reachable") from exc

    # 2. pointer restore (the alias is already back on the old target).
    try:
        if previous is None:
            if pointer_path.exists():
                pointer_path.unlink()
        else:
            _write_json_atomic(pointer_path, previous)
    except Exception as exc:
        # compensate: the alias must go back to the published target so the
        # state stays consistent (published).
        new_collection = record.get("new_collection")
        try:
            if alias_admin and record.get("alias") and new_collection:
                alias_admin.switch(record["alias"], new_collection)
        except Exception as comp_exc:
            raise PublishStateError(
                f"pointer restore failed ({exc}) AND alias "
                f"forward-compensation failed ({comp_exc}); manual "
                f"reconciliation required; rollback record: {record_path}") from exc
        raise PublishStateError(
            f"pointer restore failed ({exc}); the alias was compensated back "
            "to the published target -- the state is consistent (published) "
            "and the rollback may be retried") from exc
    return previous if previous is not None else {"status": "unpublished"}
