"""T06 -- immutable candidate & publish gate (build -> assemble -> ingest -> publish).

The pipeline becomes a verifiable state machine: no in-place overwrites of
published artifacts, every stage verifies the upstream hashes it consumes,
ingest performs ALL validation offline before it may touch the formal Qdrant
collection, publish gates the pointer switch behind a fixed set of checks and
always leaves a rollback record behind.

Contracts under test:

1. candidate-only writes for EVERY stage artifact: target absent -> write;
   existing and byte-identical -> idempotent reuse; ANY content difference ->
   hard fail without truncating the existing artifact;
2. the immutable parent-hash chain: artifact manifest (page-intermediate +
   canonicals + quality report) -> ingest manifest -> publish gate record;
   tampering ANY bound hash is detected;
3. offline preflight (schema, hash, count, policy, non-publishable markers)
   runs before any Qdrant connection;
4. the publish gate re-runs the full preflight, checks the canonical file
   set/aggregate, chunk-set identity, count equality and the COMPLETE fixed
   smoke query set; the switch uses the ON-DISK gate record (never an
   in-memory dict) and every successful switch first writes a rollback
   record that can restore the previous pointer AND alias target;
5. diagnostic (``--allow-garbled``) artifacts carry a non-publishable marker
   that assemble/ingest/publish must not bypass (T06 requirement 7);
6. metadata enrichment is idempotent and never upgrades usage_policy
   (T06 requirement 8).

Run::

    .venv/Scripts/python.exe -m pytest tests/test_t06_publish_state.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from src.application.canonical_assembly import assemble_document
from src.application.canonical_validation import compute_ids, validate_document
from src.application.publish_state import (
    FIXED_SMOKE_QUERIES,
    LIVE_ALIAS,
    LIVE_COLLECTION,
    CandidateConflictError,
    CollectionIdentityError,
    HashMismatchError,
    OfflineValidationError,
    PublishGateError,
    PublishStateError,
    build_artifact_manifest,
    build_chunk_manifest,
    build_ingest_manifest,
    canonical_payload_identity,
    load_artifact_manifest,
    load_gate_record,
    load_ingest_manifest,
    offline_preflight,
    publish_gate,
    resolve_staging_target,
    rollback_pointer,
    sha256_file,
    staging_build_collection_name,
    staging_build_identity,
    staging_collection_name,
    switch_pointer,
    verify_collection_payload_identity,
    write_artifact_manifest,
    write_candidate,
    write_canonical_candidate,
    write_chunk_manifest,
    write_gate_record,
    write_ingest_manifest,
    write_quality_report,
)
from src.application.payload_enrichment import build_enrichment
from src.application.v2_chunker import chunk_document

_SHA = "a" * 64
_CV = "corpus-t06test"

# Fixed build-identity inputs for the staging-lifecycle tests.
_PROFILE = {"model_id": "BAAI/bge-small-zh-v1.5", "model_revision": "rev-1",
            "dimension": 512}
_VECTOR_CONFIG = {"distance": "cosine", "size": 512}
_SPARSE_CONFIG = {"bm25": {"modifier": "idf", "tokenizer": "multilingual"}}
_BUILD_ID = staging_build_identity(
    corpus_version=_CV, artifact_manifest_sha256="0" * 64,
    chunk_manifest_sha256="1" * 64, embedding_profile=_PROFILE,
    vector_config=_VECTOR_CONFIG, sparse_config=_SPARSE_CONFIG,
    enrichment_version="t06.enrichment/1.0")
_TARGET = staging_build_collection_name(_CV, _BUILD_ID)


def _identity_payload(n: int, *, text: str | None = None,
                      policy: str = "answer_and_citation",
                      build_identity: str = _BUILD_ID,
                      source: str = "b" * 64,
                      file_name: str = "a.pdf") -> dict:
    """One canonicalized identity payload (the fields the digest covers)."""
    return {"chunk_id": f"chunk_{n:032d}", "text": text or f"chunk {n}",
            "usage_policy": policy, "source_sha256": source,
            "file_name": file_name, "build_identity": build_identity}


def _expected_identity(payloads: list[dict]) -> dict:
    return canonical_payload_identity(payloads, build_identity=_BUILD_ID)


def _noop_verifier(collection_name: str) -> None:
    return None


def _canonical(root: Path, corpus_version: str, sha: str = _SHA) -> dict:
    """Assemble a small but fully valid V2 canonical document."""
    page_doc = {
        "sha256": sha,
        "file_name": f"{sha[:12]}.pdf",
        "config_hash": "c" * 64,
        "processing_run_id": "run_t06",
        "metadata": {"document_kind": "standard"},
        "pages": [{
            "physical_page": 1, "page_index": 0, "display_page_label": None,
            "width": 595.0, "height": 842.0, "rotation": 0,
            "extraction_route": "full_ocr", "decision_status": "approved",
            "decision_reasons": [], "parser_name": "paddleocr-text",
            "publishable": True,
            "elements": [{
                "element_id": "p1-t1",
                "text": "排水管道设计流量应按本条规定计算。",
                "bbox": [60.0, 300.0, 500.0, 330.0],
            }],
            "regions": [], "tables": [],
        }],
    }
    doc = assemble_document(page_doc)
    doc.update(compute_ids(doc))
    validate_document(doc)
    return doc


def _write_json(path: Path, payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


class _Root:
    """Temp repo root with a candidate corpus directory + one canonical."""

    def __init__(self, *, with_canonical: bool = True, chunks: bool = True):
        import hashlib
        self.tmp = tempfile.mkdtemp(prefix="t06-")
        self.root = Path(self.tmp)
        self.cv_dir = self.root / "data" / "canonical" / "v2" / _CV
        (self.root / "data" / "registry").mkdir(parents=True, exist_ok=True)
        # page-intermediate jsonl + registry manifest binding its hash
        jsonl = self.root / "data" / "canonical" / f"{_CV}-documents.jsonl"
        jsonl.parent.mkdir(parents=True, exist_ok=True)
        jsonl.write_text('{"sha256": "stub"}\n', encoding="utf-8")
        _write_json(self.root / "data" / "registry" / f"{_CV}.json", {
            "schema_version": "1", "corpus_version": _CV, "status": "ready",
            "canonical_documents": f"data/canonical/{_CV}-documents.jsonl",
            "canonical_documents_sha256": hashlib.sha256(
                jsonl.read_bytes()).hexdigest(),
        })
        if with_canonical:
            doc = _canonical(self.root, _CV)
            _write_json(
                self.cv_dir / f"{_SHA}.canonical.json", doc)
            chunks_ = chunk_document(doc)
            if chunks:
                write_chunk_manifest(self.root, _CV, build_chunk_manifest(chunks_))
            self.doc = doc
            self.chunks = chunks_

    def __enter__(self) -> "_Root":
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tamper(self, relpath: str, *, append: bytes = b"X") -> None:
        p = self.root / relpath
        p.write_bytes(p.read_bytes() + append)


# --------------------------------------------------------------------------- #
# 1. Candidate-only writes (requirement 3)
# --------------------------------------------------------------------------- #

class TestCandidateWrites(unittest.TestCase):
    def test_write_to_absent_target_writes(self):
        with _Root(with_canonical=False) as env:
            status = write_canonical_candidate(
                env.root, _CV, "x.canonical.json", b"payload")
            self.assertEqual(status, "written")
            self.assertEqual(
                (env.cv_dir / "x.canonical.json").read_bytes(), b"payload")

    def test_identical_rewrite_reuses_idempotently(self):
        with _Root(with_canonical=False) as env:
            write_canonical_candidate(env.root, _CV, "x.canonical.json", b"same")
            status = write_canonical_candidate(
                env.root, _CV, "x.canonical.json", b"same")
            self.assertEqual(status, "reused")
            self.assertEqual(
                (env.cv_dir / "x.canonical.json").read_bytes(), b"same")

    def test_conflicting_content_hard_fails_without_truncation(self):
        with _Root(with_canonical=False) as env:
            write_canonical_candidate(env.root, _CV, "x.canonical.json", b"v1")
            with self.assertRaises(CandidateConflictError):
                write_canonical_candidate(
                    env.root, _CV, "x.canonical.json", b"v2-totally-different")
            # the existing artifact is untouched -- no truncate/overwrite
            self.assertEqual(
                (env.cv_dir / "x.canonical.json").read_bytes(), b"v1")


# --------------------------------------------------------------------------- #
# 2. Artifact manifest hash binding (requirement 2)
# --------------------------------------------------------------------------- #

class TestArtifactManifestBinding(unittest.TestCase):
    def _prepared(self) -> _Root:
        env = _Root()
        write_quality_report(env.root, _CV, [
            {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
        write_artifact_manifest(env.root, _CV)
        return env

    def test_manifest_binds_all_assemble_stages(self):
        with self._prepared() as env:
            m = load_artifact_manifest(env.root, _CV)
            self.assertEqual(m["schema_version"], "t06-3")
            self.assertEqual(m["page_intermediate"]["sha256"],
                             sha256_file(env.root / "data" / "canonical" / f"{_CV}-documents.jsonl"))
            self.assertEqual(m["canonical"]["count"], 1)
            self.assertEqual(m["canonical"]["files"],
                             {f"{_SHA}.canonical.json": sha256_file(env.cv_dir / f"{_SHA}.canonical.json")})
            self.assertEqual(m["quality_report"]["sha256"],
                             sha256_file(env.cv_dir / "quality_report.json"))

    def test_tamper_page_intermediate_detected(self):
        with self._prepared() as env:
            env.tamper(f"data/canonical/{_CV}-documents.jsonl")
            with self.assertRaises(HashMismatchError):
                load_artifact_manifest(env.root, _CV, verify=True)

    def test_tamper_canonical_detected(self):
        with self._prepared() as env:
            env.tamper(f"data/canonical/v2/{_CV}/{_SHA}.canonical.json")
            with self.assertRaises(HashMismatchError):
                load_artifact_manifest(env.root, _CV, verify=True)

    def test_tamper_quality_report_detected(self):
        with self._prepared() as env:
            env.tamper(f"data/canonical/v2/{_CV}/quality_report.json")
            with self.assertRaises(HashMismatchError):
                load_artifact_manifest(env.root, _CV, verify=True)

    def test_aggregate_changes_when_canonical_set_changes(self):
        with self._prepared() as env:
            before = load_artifact_manifest(env.root, _CV)["canonical"]["aggregate_sha256"]
            other = dict(env.doc)
            _write_json(env.cv_dir / ("f" * 64 + ".canonical.json"), other)
            after = build_artifact_manifest(env.root, _CV)["canonical"]["aggregate_sha256"]
            self.assertNotEqual(before, after)

    def test_manifest_rewrite_with_changed_set_is_a_conflict(self):
        # the immutable stage chain: a changed canonical set changes the
        # manifest bytes -> the candidate write must conflict, never rewrite
        with self._prepared() as env:
            before = (env.cv_dir / "artifact_manifest.json").read_bytes()
            other = dict(env.doc)
            _write_json(env.cv_dir / ("f" * 64 + ".canonical.json"), other)
            with self.assertRaises(CandidateConflictError):
                write_artifact_manifest(env.root, _CV)
            self.assertEqual(
                (env.cv_dir / "artifact_manifest.json").read_bytes(), before)

    def test_quality_report_rewrite_with_different_content_conflicts(self):
        with self._prepared() as env:
            before = (env.cv_dir / "quality_report.json").read_bytes()
            with self.assertRaises(CandidateConflictError):
                write_quality_report(env.root, _CV, [
                    {"sha256": _SHA, "verdict": "garbled_text",
                     "bad_pages": 1, "pages": 1}])
            self.assertEqual((env.cv_dir / "quality_report.json").read_bytes(), before)


# --------------------------------------------------------------------------- #
# 3. Offline preflight (requirements 4, 7)
# --------------------------------------------------------------------------- #

class TestOfflinePreflight(unittest.TestCase):
    def test_happy_path_passes_offline(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            report = offline_preflight(env.root, _CV)
            self.assertEqual(report["status"], "ok")

    def test_garbled_quality_report_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "garbled_text", "bad_pages": 1, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_allow_garbled_diagnostic_marker_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "garbled_text", "bad_pages": 1, "pages": 1}],
                non_publishable_reasons=["allow_garbled"])
            write_artifact_manifest(env.root, _CV)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_chunk_count_mismatch_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            # an on-disk manifest that no longer matches the canonicals
            # (simulated tampering -- the candidate write would refuse it)
            manifest = build_chunk_manifest(env.chunks)
            manifest["total_chunks"] += 1
            _write_json(env.cv_dir / "chunk_manifest.json", manifest)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_policy_mismatch_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            manifest = build_chunk_manifest(env.chunks)
            manifest["policy_counts"]["answer_and_citation"] = 999
            _write_json(env.cv_dir / "chunk_manifest.json", manifest)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_invalid_policy_value_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            manifest = build_chunk_manifest(env.chunks)
            manifest["policy_counts"] = {"made_up_policy": 3}
            _write_json(env.cv_dir / "chunk_manifest.json", manifest)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_schema_violation_blocks(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            path = env.cv_dir / f"{_SHA}.canonical.json"
            doc = json.loads(path.read_text(encoding="utf-8"))
            doc["elements"] = []
            _write_json(path, doc)
            with self.assertRaises(OfflineValidationError):
                offline_preflight(env.root, _CV)

    def test_preflight_never_imports_qdrant(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            # other tests in a full-suite run may already have imported
            # qdrant; drop every qdrant module from sys.modules so we can
            # observe whether offline_preflight itself imports one, then
            # restore the previous import state.
            snapshot = {k: v for k, v in sys.modules.items() if "qdrant" in k}
            for name in list(snapshot):
                del sys.modules[name]
            try:
                offline_preflight(env.root, _CV)
                self.assertNotIn("qdrant_client", sys.modules)
            finally:
                sys.modules.update(snapshot)


# --------------------------------------------------------------------------- #
# 4. Publish gate + pointer switch + rollback (requirements 5, 6)
# --------------------------------------------------------------------------- #

def _gate_ready(env: _Root) -> None:
    write_quality_report(env.root, _CV, [
        {"sha256": _SHA, "verdict": "readable", "bad_pages": 0, "pages": 1}])
    write_artifact_manifest(env.root, _CV)


def _full_smoke(hits: int = 3) -> list[dict]:
    """Smoke results covering the COMPLETE fixed query set."""
    return [{"query": q, "hit_count": hits} for q in FIXED_SMOKE_QUERIES]


def _chain_ready(env: _Root) -> dict:
    """Write the full immutable stage chain up to the gate record.

    quality report -> artifact manifest -> chunk manifest -> ingest report ->
    ingest manifest -> publish gate record. Returns the gate report.
    """
    _gate_ready(env)
    write_chunk_manifest(env.root, _CV, build_chunk_manifest(env.chunks))
    report = {"status": "ok", "chunks": len(env.chunks)}
    write_candidate(env.root, _CV, "ingest_report.json",
                    _write_json(env.cv_dir / "ingest_report.json", report))
    ingest_manifest = build_ingest_manifest(
        env.root, _CV,
        chunk_manifest_sha=sha256_file(env.cv_dir / "chunk_manifest.json"),
        ingest_report_sha=sha256_file(env.cv_dir / "ingest_report.json"))
    write_ingest_manifest(env.root, _CV, ingest_manifest)
    gate = publish_gate(
        env.root, _CV, point_count=len(env.chunks), vector_count=len(env.chunks),
        smoke_results=_full_smoke(), collection_name=staging_collection_name(_CV))
    write_gate_record(env.root, _CV, gate_report=gate,
                      ingest_manifest_sha=sha256_file(
                          env.cv_dir / "ingest_manifest.json"))
    return gate


class _FakeAliasAdmin:
    def __init__(self, initial: str | None = None):
        self.target = initial
        self.calls: list[tuple] = []

    def current_target(self, alias: str) -> str | None:
        return self.target

    def switch(self, alias: str, collection: str) -> None:
        self.calls.append(("switch", alias, collection))
        self.target = collection

    def remove_alias(self, alias: str) -> None:
        self.calls.append(("remove", alias))
        self.target = None


# --------------------------------------------------------------------------- #
# 3b. Staging collection immutability (T06-R3 P1-1)
# --------------------------------------------------------------------------- #

class _FakeCollectionInfo:
    def __init__(self, points: int, vector_size: int,
                 sparse: tuple = ("bm25",), unreadable: bool = False):
        self.points_count = points
        if unreadable:
            # schema that cannot be read at all -> verification must fail closed
            self.config = None
            return
        class _Vectors:
            # real Qdrant: named-vector dict {"dense": VectorParams}
            def __init__(self, size):
                self.vectors = {"dense": type("Dense", (), {"size": size})()}
        class _Params:
            def __init__(self, size, sparse):
                self.vectors = _Vectors(size).vectors
                self.sparse_vectors = {name: None for name in sparse}
        self.config = type("Config", (), {"params": _Params(vector_size, sparse)})()


class _FakeQdrant:
    """Records every destructive op; ``collections`` maps name -> points and
    ``payloads`` maps name -> stored payload dicts (for identity scrolls)."""

    def __init__(self, collections: dict[str, int] | None = None,
                 vector_size: int = 512, unreadable: bool = False,
                 sparse: tuple = ("bm25",)):
        self.collections = dict(collections or {})
        self.payloads: dict[str, list[dict]] = {}
        self.vector_size = vector_size
        self.unreadable = unreadable
        self.sparse = sparse
        self.ops: list[tuple] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def get_collection(self, name: str):
        if name not in self.collections:
            raise LookupError(name)
        return _FakeCollectionInfo(self.collections[name], self.vector_size,
                                   sparse=self.sparse, unreadable=self.unreadable)

    def scroll(self, collection_name: str, limit: int = 256, offset=None,
               with_payload: bool = True, with_vectors: bool = False):
        items = self.payloads.get(collection_name, [])
        start = offset or 0
        batch = items[start:start + limit]
        next_offset = start + len(batch)
        records = [type("Rec", (), {"payload": p})() for p in batch]
        return records, (None if next_offset >= len(items) else next_offset)

    def delete_collection(self, name: str) -> None:
        self.ops.append(("delete", name))
        self.collections.pop(name, None)

    def create_collection(self, name: str) -> None:
        self.ops.append(("create", name))
        self.collections[name] = 0
        self.payloads[name] = []

    def upsert(self, name: str, points) -> None:
        self.ops.append(("upsert", name))
        self.collections[name] = self.collections.get(name, 0) + len(points)


class TestStagingIdentityAndLifecycle(unittest.TestCase):
    """T06-R4: physical collection identity is DERIVED (stable) and verified
    against the real Qdrant payloads; any mismatch is fail-closed."""

    def _resolve(self, client, payloads, *, target=_TARGET, size=512,
                 sparse=True, guarded=None):
        return resolve_staging_target(
            client, target_name=target,
            expected=_expected_identity(payloads),
            expected_vector_size=size, sparse_enabled=sparse,
            guarded_collections=guarded)

    def test_absent_target_yields_plain_create(self):
        client = _FakeQdrant()
        payloads = [_identity_payload(n) for n in range(3)]
        name, mode = self._resolve(client, payloads)
        self.assertEqual((name, mode), (_TARGET, "create"))

    def test_full_payload_identity_match_reuses_readonly(self):
        payloads = [_identity_payload(n) for n in range(3)]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = payloads
        name, mode = self._resolve(client, payloads)
        self.assertEqual((name, mode), (_TARGET, "reuse"))
        self.assertEqual(client.ops, [])  # scroll is read-only: zero mutation

    def test_same_count_same_dim_different_chunks_hard_fails(self):
        # acceptance matrix: same point count + same vector size, but the
        # stored chunks are a DIFFERENT stream -> must NOT be reused
        stored = [_identity_payload(100 + n) for n in range(3)]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = stored
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, [_identity_payload(n) for n in range(3)])
        self.assertIn(_TARGET, client.collections)  # never deleted
        self.assertNotIn("delete", [op[0] for op in client.ops])

    def test_policy_skew_hard_fails(self):
        stored = [_identity_payload(n) for n in range(3)]
        skewed = [_identity_payload(n) for n in range(2)] + \
            [_identity_payload(2, policy="excluded")]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = skewed
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, stored)
        self.assertIn(_TARGET, client.collections)

    def test_tampered_text_hard_fails(self):
        stored = [_identity_payload(n) for n in range(3)]
        tampered = list(stored)
        tampered[1] = dict(tampered[1], text="chunk 1 TAMPERED")
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = tampered
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, stored)

    def test_tampered_source_identity_hard_fails(self):
        stored = [_identity_payload(n) for n in range(3)]
        tampered = [_identity_payload(n) for n in range(2)] + \
            [_identity_payload(2, source="c" * 64)]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = tampered
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, stored)

    def test_point_count_mismatch_hard_fails(self):
        payloads = [_identity_payload(n) for n in range(3)]
        client = _FakeQdrant({_TARGET: 7})
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads)

    def test_unreadable_vector_schema_fails_closed(self):
        payloads = [_identity_payload(n) for n in range(3)]
        client = _FakeQdrant({_TARGET: 3}, unreadable=True)
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads)
        self.assertEqual(client.ops, [])  # refused BEFORE any build/mutation

    def test_sparse_schema_missing_fails_closed(self):
        payloads = [_identity_payload(n) for n in range(3)]
        client = _FakeQdrant({_TARGET: 3}, sparse=())
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads, sparse=True)

    def test_missing_payload_field_fails_closed(self):
        payloads = [_identity_payload(n) for n in range(2)]
        broken = [dict(_identity_payload(2))]
        del broken[0]["file_name"]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = payloads + broken
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads + broken)

    def test_foreign_build_identity_in_payload_fails_closed(self):
        payloads = [_identity_payload(n) for n in range(3)]
        stored = [_identity_payload(n, build_identity="bi-foreign") for n in range(3)]
        client = _FakeQdrant({_TARGET: 3})
        client.payloads[_TARGET] = stored
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads)

    def test_live_collection_is_never_a_build_target(self):
        payloads = [_identity_payload(n) for n in range(3)]
        client = _FakeQdrant()
        with self.assertRaises(CollectionIdentityError):
            self._resolve(client, payloads, target=LIVE_COLLECTION)

    def test_derived_identity_is_stable_and_profile_sensitive(self):
        kwargs = dict(
            corpus_version=_CV, artifact_manifest_sha256="0" * 64,
            chunk_manifest_sha256="1" * 64, embedding_profile=_PROFILE,
            vector_config=_VECTOR_CONFIG, sparse_config=_SPARSE_CONFIG,
            enrichment_version="t06.enrichment/1.0")
        again = staging_build_identity(**kwargs)
        self.assertEqual(_BUILD_ID, again)  # deterministic across reruns
        other_model = staging_build_identity(
            **{**kwargs, "embedding_profile": {**_PROFILE,
                                               "model_revision": "rev-2"}})
        self.assertNotEqual(_BUILD_ID, other_model)  # profile change -> new name
        other_manifest = staging_build_identity(
            **{**kwargs, "chunk_manifest_sha256": "2" * 64})
        self.assertNotEqual(_BUILD_ID, other_manifest)
        name = staging_build_collection_name(_CV, _BUILD_ID)
        self.assertTrue(name.startswith(staging_collection_name(_CV)))
        self.assertNotEqual(name, LIVE_COLLECTION)


class TestPublishGate(unittest.TestCase):
    _STAGING = staging_collection_name("corpus-t06test")

    def _gate(self, env, **overrides) -> dict:
        kwargs = dict(
            point_count=len(env.chunks), vector_count=len(env.chunks),
            smoke_results=_full_smoke(), collection_name=self._STAGING)
        kwargs.update(overrides)
        return publish_gate(env.root, _CV, **kwargs)

    def test_gate_passes_when_all_checks_hold(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env)
            self.assertEqual(gate["gate"], "passed", gate["failures"])

    def test_gate_rejects_live_collection_name(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env, collection_name=LIVE_COLLECTION)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("staging" in f for f in gate["failures"]))

    def test_gate_fails_when_artifact_manifest_missing(self):
        with _Root() as env:
            _gate_ready(env)
            (env.cv_dir / "artifact_manifest.json").unlink()
            gate = self._gate(env)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("artifact_manifest.json missing" in f
                                for f in gate["failures"]))

    def test_gate_fails_on_empty_smoke(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env, smoke_results=[])
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("smoke results empty" in f for f in gate["failures"]))

    def test_gate_fails_when_fixed_query_missing(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env, smoke_results=_full_smoke()[:1])
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("missing from the fixed set" in f
                                for f in gate["failures"]))

    def test_gate_fails_on_duplicate_smoke_query(self):
        with _Root() as env:
            _gate_ready(env)
            results = [{"query": FIXED_SMOKE_QUERIES[0], "hit_count": 3}] * 2
            gate = self._gate(env, smoke_results=results)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("duplicate smoke queries" in f
                                for f in gate["failures"]))

    def test_gate_fails_on_smoke_query_outside_fixed_set(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env, smoke_results=_full_smoke()
                              + [{"query": "意外的额外查询", "hit_count": 1}])
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("outside the fixed set" in f
                                for f in gate["failures"]))

    def test_gate_fails_on_zero_hit_query(self):
        with _Root() as env:
            _gate_ready(env)
            results = _full_smoke()
            results[1] = dict(results[1], hit_count=0)
            gate = self._gate(env, smoke_results=results)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("no hits" in f for f in gate["failures"]))

    def test_gate_fails_on_count_mismatch(self):
        with _Root() as env:
            _gate_ready(env)
            gate = self._gate(env, point_count=len(env.chunks) + 1)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("count mismatch" in f for f in gate["failures"]))

    def test_gate_fails_on_extra_canonical_file(self):
        with _Root() as env:
            _gate_ready(env)
            _write_json(env.cv_dir / ("f" * 64 + ".canonical.json"), env.doc)
            gate = self._gate(env)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("extra canonical files" in f
                                for f in gate["failures"]))

    def test_gate_fails_on_chunk_set_mismatch(self):
        with _Root() as env:
            _gate_ready(env)
            tampered = build_chunk_manifest(env.chunks)
            tampered["chunk_set_sha256"] = "0" * 64
            (env.cv_dir / "chunk_manifest.json").write_bytes(
                _write_json(env.cv_dir / "chunk_manifest.json", tampered))
            gate = self._gate(env)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("chunk-set hash mismatch" in f
                                for f in gate["failures"]))

    def test_gate_fails_when_garbled_present(self):
        with _Root() as env:
            write_quality_report(env.root, _CV, [
                {"sha256": _SHA, "verdict": "garbled_text", "bad_pages": 1, "pages": 1}])
            write_artifact_manifest(env.root, _CV)
            gate = self._gate(env)
            self.assertEqual(gate["gate"], "failed")
            self.assertTrue(any("garbled" in f for f in gate["failures"]))


class TestStageChain(unittest.TestCase):
    """ingest manifest + gate record: immutable parent-hash chain."""

    def test_ingest_manifest_binds_and_verifies_parent_hashes(self):
        with _Root() as env:
            _chain_ready(env)
            m = load_ingest_manifest(env.root, _CV, verify=True)
            self.assertEqual(m["collection_name"], staging_collection_name(_CV))
            self.assertEqual(m["artifact_manifest_sha256"],
                             sha256_file(env.cv_dir / "artifact_manifest.json"))

    def test_chunk_manifest_global_policy_counts_are_populated(self):
        # T06-R4: the TOP-LEVEL policy_counts must equal the per-document
        # totals (previously never incremented -> the gate comparison was
        # vacuously true)
        with _Root() as env:
            manifest = build_chunk_manifest(env.chunks)
            per_doc = manifest["per_document"]
            totals: dict[str, int] = {}
            for entry in per_doc.values():
                for policy, count in entry["policy_counts"].items():
                    totals[policy] = totals.get(policy, 0) + count
            self.assertEqual(manifest["policy_counts"], dict(sorted(totals.items())))
            self.assertTrue(manifest["policy_counts"])

    def test_tampered_chunk_manifest_breaks_ingest_manifest_verify(self):
        with _Root() as env:
            _chain_ready(env)
            env.tamper("data/canonical/v2/corpus-t06test/chunk_manifest.json")
            with self.assertRaises(HashMismatchError):
                load_ingest_manifest(env.root, _CV, verify=True)

    def test_tampered_ingest_report_breaks_ingest_manifest_verify(self):
        with _Root() as env:
            _chain_ready(env)
            env.tamper("data/canonical/v2/corpus-t06test/ingest_report.json")
            with self.assertRaises(HashMismatchError):
                load_ingest_manifest(env.root, _CV, verify=True)

    def test_gate_record_binds_candidate_and_collection(self):
        with _Root() as env:
            _chain_ready(env)
            record = load_gate_record(env.root, _CV, verify=True)
            self.assertEqual(record["gate"], "passed")
            self.assertEqual(record["collection_name"],
                             staging_collection_name(_CV))

    def test_tampered_ingest_manifest_breaks_gate_record_verify(self):
        with _Root() as env:
            _chain_ready(env)
            env.tamper("data/canonical/v2/corpus-t06test/ingest_manifest.json")
            with self.assertRaises(HashMismatchError):
                load_gate_record(env.root, _CV, verify=True)

    def test_forged_gate_record_is_refused(self):
        # a hand-written "passed" record whose bound hashes do not match the
        # on-disk chain must be rejected by verification
        with _Root() as env:
            _chain_ready(env)
            forged = json.loads(
                (env.cv_dir / "publish_gate_record.json").read_text(encoding="utf-8"))
            forged["artifact_manifest_sha256"] = "0" * 64
            (env.cv_dir / "publish_gate_record.json").write_bytes(
                _write_json(env.cv_dir / "publish_gate_record.json", forged))
            with self.assertRaises(HashMismatchError):
                load_gate_record(env.root, _CV, verify=True)


class TestSwitchPointerAndRollback(unittest.TestCase):
    def test_switch_uses_on_disk_gate_record_and_switches_both_targets(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer_path, {"corpus_version": "corpus-old",
                                       "status": "published"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            current = json.loads(pointer_path.read_text(encoding="utf-8"))
            self.assertEqual(current["corpus_version"], _CV)
            # the pointer records the alias (runtime entry) AND the target
            self.assertEqual(current["alias"], LIVE_ALIAS)
            self.assertEqual(current["collection_name"],
                             staging_collection_name(_CV))
            self.assertEqual(current["chunk_count"], len(env.chunks))
            self.assertEqual(admin.calls, [("switch", LIVE_ALIAS,
                                            staging_collection_name(_CV))])
            records = list((env.root / "data" / "registry" / "rollback").glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["previous_pointer"]["corpus_version"], "corpus-old")
            self.assertEqual(record["previous_collection"], "corpus_v2_old_target")
            self.assertEqual(record["new_collection"], staging_collection_name(_CV))

    def test_rollback_restores_pointer_and_alias_target(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous_bytes = _write_json(
                pointer_path, {"corpus_version": "corpus-old", "status": "published"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertNotEqual(pointer_path.read_bytes(), previous_bytes)
            restored = rollback_pointer(env.root, alias_admin=admin)
            self.assertEqual(restored["corpus_version"], "corpus-old")
            self.assertEqual(pointer_path.read_bytes(), previous_bytes)
            self.assertEqual(admin.target, "corpus_v2_old_target")

    def test_switch_refused_without_on_disk_gate_record(self):
        with _Root() as env:
            _gate_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises(PublishGateError):
                switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])
            self.assertFalse((env.root / "data" / "registry" / "rollback").exists())

    def test_switch_refused_when_gate_record_says_failed(self):
        with _Root() as env:
            _chain_ready(env)
            # replace the passed record with a failed one (hand-forged bytes)
            failed = publish_gate(
                env.root, _CV, point_count=0, vector_count=0,
                smoke_results=[], collection_name=staging_collection_name(_CV))
            (env.cv_dir / "publish_gate_record.json").unlink()
            write_gate_record(env.root, _CV, gate_report=failed,
                              ingest_manifest_sha=sha256_file(
                                  env.cv_dir / "ingest_manifest.json"))
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises(PublishGateError):
                switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])

    def test_switch_refused_when_chain_tampered_after_gate(self):
        with _Root() as env:
            _chain_ready(env)
            env.tamper("data/canonical/v2/corpus-t06test/ingest_report.json")
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises((HashMismatchError, PublishGateError)):
                switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])

    def test_switch_refused_on_artifact_manifest_tampering(self):
        with _Root() as env:
            _chain_ready(env)
            env.tamper("data/canonical/v2/corpus-t06test/artifact_manifest.json")
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises(PublishStateError):
                switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])


class TestPublishCompensation(unittest.TestCase):
    """T06-R3 P1-3: alias/pointer splits must self-heal or fail loudly."""

    def test_pointer_write_failure_compensates_alias_back(self):
        from src.application import publish_state as ps
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            original = ps._write_json_atomic

            def exploding(path, payload):
                if path.name == "corpus-current.json":
                    raise OSError("disk full during pointer switch")
                return original(path, payload)

            ps._write_json_atomic = exploding
            try:
                with self.assertRaises(PublishStateError):
                    switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            finally:
                ps._write_json_atomic = original
            # compensation: alias restored to the previous target, pointer
            # untouched -- the state is consistent (pre-publish), not split
            self.assertEqual(admin.target, "corpus_v2_old_target")
            self.assertEqual(pointer_path.read_bytes(), previous)
            # and the switch is retryable after the fault clears
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            self.assertEqual(admin.target, staging_collection_name(_CV))
            self.assertEqual(
                json.loads(pointer_path.read_text(encoding="utf-8"))["corpus_version"],
                _CV)

    def test_rollback_alias_failure_compensates_pointer_back(self):
        from src.application import publish_state as ps
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            published_bytes = pointer_path.read_bytes()

            class DeadAliasAdmin(_FakeAliasAdmin):
                def switch(self, alias, collection):
                    raise OSError("qdrant unreachable during rollback")

            with self.assertRaises(PublishStateError):
                rollback_pointer(env.root, alias_admin=DeadAliasAdmin())
            # compensation: the pointer is back at the PUBLISHED state -- the
            # rollback failed CLOSED, never half-applied
            self.assertEqual(pointer_path.read_bytes(), published_bytes)

    def test_rollback_total_failure_is_reported_not_split(self):
        from src.application import publish_state as ps
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            published_bytes = pointer_path.read_bytes()

            class DeadAliasAdmin(_FakeAliasAdmin):
                def switch(self, alias, collection):
                    raise OSError("qdrant unreachable")

            original = ps._write_json_atomic

            def exploding(path, payload):
                if path.name == "corpus-current.json":
                    raise OSError("disk full")
                return original(path, payload)

            ps._write_json_atomic = exploding
            try:
                with self.assertRaises(PublishStateError) as ctx:
                    rollback_pointer(env.root, alias_admin=DeadAliasAdmin())
            finally:
                ps._write_json_atomic = original
            self.assertIn("manual reconciliation", str(ctx.exception))
            # nothing silently half-applied: pointer still published
            self.assertEqual(pointer_path.read_bytes(), published_bytes)


class TestPublishAuthorization(unittest.TestCase):
    """T06-R4: the alias admin + collection verifier are MANDATORY for
    publish/rollback -- a pointer-only switch is a split state by definition.
    Every refusal happens BEFORE the rollback record, the alias or the
    pointer changes."""

    def test_publish_refused_without_alias_admin(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            with self.assertRaises(PublishStateError):
                switch_pointer(env.root, _CV, alias_admin=None,
                               verify_collection=_noop_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertFalse((env.root / "data" / "registry" / "rollback").exists())

    def test_publish_refused_without_collection_verifier(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises(PublishStateError):
                switch_pointer(env.root, _CV, alias_admin=admin,
                               verify_collection=None)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])
            self.assertFalse((env.root / "data" / "registry" / "rollback").exists())

    def test_verifier_failure_aborts_before_any_state_change(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            previous = _write_json(pointer_path, {"corpus_version": "corpus-old"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")

            def failing_verifier(collection_name: str) -> None:
                raise CollectionIdentityError(
                    f"collection {collection_name!r} failed re-verification")

            with self.assertRaises(CollectionIdentityError):
                switch_pointer(env.root, _CV, alias_admin=admin,
                               verify_collection=failing_verifier)
            self.assertEqual(pointer_path.read_bytes(), previous)
            self.assertEqual(admin.calls, [])  # alias untouched
            self.assertFalse((env.root / "data" / "registry" / "rollback").exists())

    def test_rollback_refused_without_alias_admin(self):
        with _Root() as env:
            _chain_ready(env)
            pointer_path = env.root / "data" / "registry" / "corpus-current.json"
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            switch_pointer(env.root, _CV, alias_admin=admin,
                           verify_collection=_noop_verifier)
            published_bytes = pointer_path.read_bytes()
            with self.assertRaises(PublishStateError):
                rollback_pointer(env.root, alias_admin=None)
            # refused BEFORE anything moved: pointer + alias still published
            self.assertEqual(pointer_path.read_bytes(), published_bytes)
            self.assertEqual(admin.target, staging_collection_name(_CV))


class TestCreateOnlyBuild(unittest.TestCase):
    """T06-R3 P1-1: the build path is create-only against a real local client."""

    _CHUNKS = [
        {"chunk_id": f"chunk_{n:032d}", "text": f"chunk {n}",
         "evidence_ids": [f"ev_{n:032d}"],
         "primary_evidence_id": f"ev_{n:032d}",
         "document_version_id": "doc_x", "evidence_type": "paragraph",
         "physical_pages": [1],
         "metadata": {"usage_policy": "answer_and_citation"}}
        for n in (1, 2, 3)
    ]

    def test_create_only_build_refuses_existing_collection(self):
        from qdrant_client import QdrantClient

        from src.retrieval.qdrant_index import QdrantRetrievalIndex
        client = QdrantClient(":memory:")
        index = QdrantRetrievalIndex(
            client=client, collection_name="t06_immut_stage", vector_size=2)
        index.build(self._CHUNKS, [[1.0, 0.0]] * 3, create_only=True)
        self.assertEqual(client.count("t06_immut_stage", exact=True).count, 3)
        with self.assertRaises(Exception):
            index.build(self._CHUNKS, [[1.0, 0.0]] * 3, create_only=True)
        # the original points survived -- no delete/recreate happened
        self.assertEqual(client.count("t06_immut_stage", exact=True).count, 3)

    def test_resolve_target_reuses_verified_memory_collection(self):
        from qdrant_client import QdrantClient

        from src.retrieval.qdrant_index import QdrantRetrievalIndex
        client = QdrantClient(":memory:")
        index = QdrantRetrievalIndex(
            client=client, collection_name=_TARGET,
            vector_size=2)
        payloads = [dict(_identity_payload(n), evidence_ids=[],
                         primary_evidence_id=f"ev_{n}")
                    for n in range(3)]
        index.build(payloads, [[1.0, 0.0]] * 3, create_only=True)
        name, mode = resolve_staging_target(
            client, target_name=_TARGET,
            expected=canonical_payload_identity(payloads, build_identity=_BUILD_ID),
            expected_vector_size=2, sparse_enabled=False)
        self.assertEqual((name, mode), (_TARGET, "reuse"))


# --------------------------------------------------------------------------- #
# 5. Enrichment idempotency + policy invariance (requirement 8)
# --------------------------------------------------------------------------- #

class TestEnrichmentPolicyInvariance(unittest.TestCase):
    _MD = {"file_name": "a.pdf", "standard_number": "GB 50-2020"}

    def test_missing_policy_backfilled_locator_only(self):
        enriched = build_enrichment({"chunk_id": "chunk_" + "0" * 32}, self._MD, "")
        self.assertEqual(enriched["usage_policy"], "source_locator_only")

    def test_existing_policy_never_upgraded(self):
        for policy in ("source_locator_only", "answer_and_citation", "excluded"):
            payload = {"chunk_id": "chunk_" + "0" * 32, "usage_policy": policy}
            enriched = build_enrichment(payload, self._MD, "")
            self.assertNotIn("usage_policy", enriched)
            self.assertEqual(payload["usage_policy"], policy)

    def test_enrichment_is_idempotent(self):
        payload = {"chunk_id": "chunk_" + "0" * 32, "usage_policy": "excluded"}
        once = build_enrichment(payload, self._MD, "http://x")
        merged = {**payload, **once}
        twice = build_enrichment(merged, self._MD, "http://x")
        self.assertEqual({**merged, **twice}, merged)


if __name__ == "__main__":
    unittest.main()
