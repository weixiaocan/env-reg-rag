"""T06 -- pipeline scripts as a verifiable state machine.

Covers the reviewer's scenario list at the script level:

* assemble writes candidates only (conflict / idempotent re-run / upstream
  page-intermediate hash verification);
* ``--allow-garbled`` diagnostic artifacts are marked non-publishable and
  cannot bypass ingest;
* ingest performs all offline validation BEFORE touching Qdrant (a sentinel
  builder asserts no connection on offline failure) and gates the pointer
  switch on the publish checks;
* the retired QXT in-place patch writes recovery overrides only -- never the
  published page-intermediate jsonl -- and a unified-build override input
  produces a NEW corpus version.

No test in this module connects to Qdrant (the builder is injected) and no
test writes into the real ``data/`` tree -- everything runs on temp roots.

Run::

    .venv/Scripts/python.exe -m pytest tests/test_t06_pipeline_scripts.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from src.application.publish_state import (  # noqa: E402
    FIXED_SMOKE_QUERIES,
    rollback_pointer,
    sha256_file,
)

_SHA = "a" * 64
_CV = "corpus-t06scripts"


def _page_doc(sha: str = _SHA, *, text: str = "排水管道设计流量应按本条规定计算。",
              decision: str = "approved") -> dict:
    return {
        "sha256": sha,
        "file_name": f"{sha[:12]}.pdf",
        "config_hash": "c" * 64,
        "processing_run_id": "run_t06",
        "metadata": {"document_kind": "standard"},
        "pages": [{
            "physical_page": 1, "page_index": 0, "display_page_label": None,
            "width": 595.0, "height": 842.0, "rotation": 0,
            "extraction_route": "full_ocr", "decision_status": decision,
            "decision_reasons": [], "parser_name": "paddleocr-text",
            "publishable": decision != "quarantine",
            "text": text,
            "elements": [{
                "element_id": "p1-t1", "text": text,
                "bbox": [60.0, 300.0, 500.0, 330.0],
            }],
            "regions": [], "tables": [],
        }],
    }


def _garbled_page_doc(sha: str) -> dict:
    # long non-Han junk on several "pages" -> document_text_quality garbled
    junk = "�" * 400
    return {
        **_page_doc(sha, text=junk),
        "pages": [
            {**_page_doc(sha, text=junk)["pages"][0], "physical_page": n}
            for n in (1, 2, 3)
        ],
    }


class _ScriptRoot:
    """Temp repo root pre-shaped for the assemble/ingest scripts."""

    def __init__(self, *, page_doc: dict | None = None, sha: str = _SHA):
        self.tmp = tempfile.mkdtemp(prefix="t06-scripts-")
        self.root = Path(self.tmp)
        (self.root / "data" / "registry").mkdir(parents=True)
        (self.root / "data" / "canonical").mkdir(parents=True)
        (self.root / "data" / "model_runtime" / "corpus_formulas" / "run_t06").mkdir(
            parents=True)
        self.sha = sha
        self.cv = _CV
        self.page_doc = page_doc or _page_doc(sha)
        self._write_jsonl()

    def _write_jsonl(self) -> None:
        rows = self.page_doc if isinstance(self.page_doc, list) else [self.page_doc]
        path = self.root / "data" / "canonical" / f"{self.cv}-documents.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        # registry manifest binds the page-intermediate (build-stage output)
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _write_json(self.root / "data" / "registry" / f"{self.cv}.json", {
            "schema_version": "1", "corpus_version": self.cv, "status": "ready",
            "canonical_documents": f"data/canonical/{self.cv}-documents.jsonl",
            "canonical_documents_sha256": digest,
        })

    def rewrite_page_docs(self, rows: list[dict]) -> None:
        self.page_doc = rows
        self._write_jsonl()

    def __enter__(self) -> "_ScriptRoot":
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _assemble(root: Path, cv: str, extra: list[str] | None = None):
    import scripts.assemble_corpus_v2 as asm
    argv = ["--corpus-version", cv, "--root", str(root)] + (extra or [])
    return asm.main(argv)


# --------------------------------------------------------------------------- #
# Assemble: candidate-only writes + upstream hash verification
# --------------------------------------------------------------------------- #

class TestAssembleCandidateSemantics(unittest.TestCase):
    def test_assemble_writes_candidate_and_artifact_manifest(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            out = env.root / "data" / "canonical" / "v2" / env.cv
            self.assertTrue((out / f"{env.sha}.canonical.json").is_file())
            manifest = json.loads(
                (out / "artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["canonical"]["count"], 1)
            self.assertTrue(manifest["publishable"])
            quality = json.loads(
                (out / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(quality["garbled_documents"], 0)

    def test_repeated_assemble_is_idempotent(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            first = (env.root / "data" / "canonical" / "v2" / env.cv
                     / f"{env.sha}.canonical.json").read_bytes()
            self.assertEqual(_assemble(env.root, env.cv), 0)
            second = (env.root / "data" / "canonical" / "v2" / env.cv
                      / f"{env.sha}.canonical.json").read_bytes()
            self.assertEqual(first, second)

    def test_conflicting_candidate_content_hard_fails_without_truncation(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            out = env.root / "data" / "canonical" / "v2" / env.cv / f"{env.sha}.canonical.json"
            before = out.read_bytes()
            manifest_before = (out.parent / "artifact_manifest.json").read_bytes()
            # a different page-intermediate produces different canonical bytes;
            # the existing candidate must NOT be truncated/overwritten
            env.rewrite_page_docs([_page_doc(env.sha, text="修改后的另一版本正文内容。")])
            rc = _assemble(env.root, env.cv)
            self.assertEqual(rc, 1, "candidate conflict must fail the run")
            self.assertEqual(out.read_bytes(), before)
            # and a failed run must not touch the released artifact manifest
            self.assertEqual((out.parent / "artifact_manifest.json").read_bytes(),
                             manifest_before)

    def test_assemble_verifies_upstream_page_intermediate_hash(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            # tamper the page-intermediate AFTER the registry manifest bound it
            jsonl = env.root / "data" / "canonical" / f"{env.cv}-documents.jsonl"
            row = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
            row["pages"][0]["decision_reasons"] = ["tampered"]
            jsonl.write_text(
                json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(Exception):
                _assemble(env.root, env.cv)


class TestAllowGarbledDiagnostic(unittest.TestCase):
    def test_allow_garbled_produces_non_publishable_marker(self):
        sha = "b" * 64
        with _ScriptRoot(page_doc=_garbled_page_doc(sha)) as env:
            rc = _assemble(env.root, env.cv, ["--allow-garbled"])
            self.assertEqual(rc, 0)
            out = env.root / "data" / "canonical" / "v2" / env.cv
            manifest = json.loads(
                (out / "artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["publishable"])
            self.assertIn("allow_garbled", manifest["non_publishable_reasons"])

    def test_non_publishable_candidate_cannot_be_ingested(self):
        from src.application.publish_state import offline_preflight
        sha = "b" * 64
        with _ScriptRoot(page_doc=_garbled_page_doc(sha)) as env:
            _assemble(env.root, env.cv, ["--allow-garbled"])
            with self.assertRaises(Exception):
                offline_preflight(env.root, env.cv)


# --------------------------------------------------------------------------- #
# Ingest: staging collection, offline-first gate, rollback (fakes injected)
# --------------------------------------------------------------------------- #

def _fake_builder(*, smoke_hits: int = 5):
    """Deterministic staging build: records nothing, returns bytes-stable
    report data (no timings) so re-runs stay idempotent."""
    def builder(chunks, qdrant_url, root, *, collection_name,
                corpus_version="", build_identity="", expected_identity=None):
        return {
            "collection": collection_name, "qdrant_url": qdrant_url,
            "build_status": "ok",
            "vector_count": len(chunks), "point_count": len(chunks),
            "search_smoke": [{"query": q, "hit_count": smoke_hits,
                              "top_chunk_id": None, "top_file_name": None,
                              "top_score": 1.0, "top_text_head": None}
                             for q in FIXED_SMOKE_QUERIES],
        }
    return builder


class _FakeAliasAdmin:
    def __init__(self, initial: str | None = None):
        self.target = initial
        self.calls: list[tuple] = []

    def current_target(self, alias):
        return self.target

    def switch(self, alias, collection):
        self.calls.append(("switch", alias, collection))
        self.target = collection

    def remove_alias(self, alias):
        self.calls.append(("remove", alias))
        self.target = None

    def verify_payload_identity(self, collection_name, *, expected,
                                expected_vector_size, sparse_enabled):
        # the real admin delegates to the full-scroll verification; the fake
        # only records that the pre-switch re-verification ran
        self.calls.append(("verify", collection_name))


class TestIngestStateMachine(unittest.TestCase):
    def _ingest(self, env: _ScriptRoot, *, publish: bool, builder=None,
                admin=None, admin_factory=None):
        import scripts.ingest_corpus_v2 as ing
        return ing.ingest(
            qdrant_url="http://sentinel.invalid:6333",
            corpus_version=env.cv, publish=publish,
            root=env.root,
            _builder=builder or _fake_builder(),
            _alias_admin=admin,
            _admin_factory=admin_factory,
        )

    def test_publish_false_builds_staging_never_live(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            before = pointer.read_bytes()
            seen = {}
            def builder(chunks, qdrant_url, root, *, collection_name,
                        corpus_version="", build_identity="",
                        expected_identity=None):
                seen["collection"] = collection_name
                seen["build_identity"] = build_identity
                return _fake_builder()(
                    chunks, qdrant_url, root, collection_name=collection_name)
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=False, builder=builder,
                                  admin=admin)
            self.assertEqual(report["status"], "ok")
            # a STABLE build-identity staging collection, never the live one
            self.assertTrue(seen["collection"].startswith(
                f"corpus_v2__staging__{env.cv}__"))
            self.assertNotEqual(seen["collection"], "corpus_v2")
            # the physical name is derived from the build identity
            self.assertTrue(seen["build_identity"].startswith("bi-"))
            self.assertEqual(seen["collection"],
                             f"corpus_v2__staging__{env.cv}__"
                             f"{seen['build_identity']}")
            self.assertEqual(report["staging_collection"], seen["collection"])
            # live alias + pointer untouched
            self.assertEqual(admin.calls, [])
            self.assertEqual(pointer.read_bytes(), before)
            self.assertFalse(
                (env.root / "data" / "registry" / "rollback").exists())

    def test_offline_failure_never_reaches_qdrant(self):
        with _ScriptRoot() as env:
            # assemble a good candidate first
            _assemble(env.root, env.cv)
            # delete the artifact manifest -> offline verification fails
            (env.root / "data" / "canonical" / "v2" / env.cv
             / "artifact_manifest.json").unlink()
            calls = []
            def sentinel(*a, **k):
                calls.append(1)
                raise AssertionError("Qdrant must not be touched on offline failure")
            with self.assertRaises(Exception):
                self._ingest(env, publish=False, builder=sentinel)
            self.assertEqual(calls, [])
            # no pointer write, no rollback record
            self.assertFalse((env.root / "data" / "registry" / "rollback").exists())

    def test_happy_path_publishes_with_rollback_record(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            # previous published corpus exists, live alias points at it
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=True, admin=admin)
            self.assertEqual(report["status"], "ok", report.get("publish_gate"))
            staged = report["staging_collection"]
            current = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(current["corpus_version"], env.cv)
            # the pointer now names the STABLE build-identity staging
            # collection AND records its build identity
            self.assertEqual(current["collection_name"], staged)
            self.assertEqual(current["build_identity"],
                             report["build_identity"])
            # T06-R4: the collection identity is re-verified BEFORE the
            # switch, then the alias is switched to the staged collection
            self.assertEqual(admin.calls, [("verify", staged),
                                           ("switch", "corpus_v2_live", staged)])
            records = list((env.root / "data" / "registry" / "rollback").glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["previous_pointer"]["corpus_version"], "corpus-old")
            self.assertEqual(record["previous_collection"], "corpus_v2_old_target")
            # the parent-hash chain binds the ingest report + build identity
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            ingest_manifest = json.loads(
                (cv_dir / "ingest_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(ingest_manifest["ingest_report_sha256"],
                             sha256_file(cv_dir / "ingest_report.json"))
            self.assertEqual(ingest_manifest["build_identity"],
                             report["build_identity"])
            self.assertIsNotNone(
                json.loads((cv_dir / "publish_gate_record.json")
                           .read_text(encoding="utf-8"))["gate"] == "passed")

    def test_staging_build_failure_leaves_live_untouched(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            before = pointer.read_bytes()
            def builder(chunks, qdrant_url, root, *, collection_name,
                        corpus_version="", build_identity="",
                        expected_identity=None):
                raise RuntimeError("staging upserts exploded")
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=True, builder=builder, admin=admin)
            self.assertEqual(report["status"], "failed")
            self.assertIn("staging upserts exploded",
                          report["build"]["error"])
            self.assertEqual(admin.calls, [])
            self.assertEqual(pointer.read_bytes(), before)
            self.assertFalse(
                (env.root / "data" / "registry" / "rollback").exists())

    def test_smoke_failure_does_not_touch_live_state(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            before = pointer.read_bytes()
            def builder(chunks, qdrant_url, root, *, collection_name,
                        corpus_version="", build_identity="",
                        expected_identity=None):
                return _fake_builder(smoke_hits=0)(
                    chunks, qdrant_url, root, collection_name=collection_name)
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=True, builder=builder, admin=admin)
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(pointer.read_bytes(), before)
            self.assertEqual(admin.calls, [])
            self.assertFalse(
                (env.root / "data" / "registry" / "rollback").exists())
            # a failed gate writes NO switchable gate record
            self.assertFalse(
                (env.root / "data" / "canonical" / "v2" / env.cv
                 / "publish_gate_record.json").exists())

    def test_publish_without_alias_admin_hard_fails_before_any_state_change(self):
        # T06-R4: a pointer-only switch is a split state by definition --
        # the missing admin is a HARD failure, before rollback record, alias
        # or pointer change
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            before = pointer.read_bytes()
            from src.application.publish_state import PublishStateError
            with self.assertRaises(PublishStateError):
                self._ingest(env, publish=True, admin=None,
                             admin_factory=lambda url: None)
            self.assertEqual(pointer.read_bytes(), before)
            self.assertFalse(
                (env.root / "data" / "registry" / "rollback").exists())

    def test_stage_artifact_conflict_hard_fails(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            self._ingest(env, publish=False)
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            # tamper a released stage artifact -> the next ingest run must
            # hit the candidate conflict instead of overwriting it
            report = json.loads(
                (cv_dir / "ingest_report.json").read_text(encoding="utf-8"))
            report["total_chunks"] += 5
            _write_json(cv_dir / "ingest_report.json", report)
            data = (cv_dir / "ingest_report.json").read_bytes()
            with self.assertRaises(Exception):
                self._ingest(env, publish=False)
            self.assertEqual((cv_dir / "ingest_report.json").read_bytes(), data)

    def test_reingest_rewrites_identical_stage_artifacts(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            self._ingest(env, publish=False)
            names = ("chunk_manifest.json", "ingest_report.json",
                     "ingest_manifest.json", "publish_gate_record.json")
            first = {n: (cv_dir / n).read_bytes() for n in names}
            self._ingest(env, publish=False)
            second = {n: (cv_dir / n).read_bytes() for n in names}
            self.assertEqual(first, second)


class _FakeQdrantState:
    """Duck-typed collection registry backing resolve_staging_target +
    the full-scroll payload identity verification (payloads AND vectors)."""

    def __init__(self, collections=None, vector_size=512, sparse=("bm25",)):
        self.collections = dict(collections or {})
        self.payloads: dict[str, list[dict]] = {}
        # per-collection list of per-point dense vectors; None entry = a
        # point that lost its vector (fail-closed verification target)
        self.vectors: dict[str, list] = {}
        self.vector_size = vector_size
        self.sparse = sparse
        self.ops: list[tuple] = []

    def collection_exists(self, name):
        return name in self.collections

    def get_collection(self, name):
        info = type("Info", (), {})()
        info.points_count = self.collections[name]
        params = type("Params", (), {})()
        # real Qdrant: named-vector dict + sparse vector map
        params.vectors = {
            "dense": type("Dense", (), {"size": self.vector_size})()}
        params.sparse_vectors = {n: None for n in self.sparse}
        info.config = type("Config", (), {"params": params})()
        return info

    def scroll(self, collection_name, limit=256, offset=None,
               with_payload=True, with_vectors=False):
        items = self.payloads.get(collection_name, [])
        vecs = self.vectors.get(collection_name)
        start = offset or 0
        batch = items[start:start + limit]
        next_offset = start + len(batch)
        records = []
        for i, p in enumerate(batch):
            vec = vecs[start + i] if vecs is not None else \
                {"dense": [0.0] * self.vector_size}
            records.append(type("Rec", (), {"payload": p, "vector": vec})())
        return records, (None if next_offset >= len(items) else next_offset)

    def get_aliases(self):
        return type("Aliases", (), {"aliases": []})()

    def delete_collection(self, name):
        self.ops.append(("delete", name))
        self.collections.pop(name, None)

    def create_collection(self, name):
        self.ops.append(("create", name))
        self.collections[name] = 0

    def upsert(self, name, points):
        self.ops.append(("upsert", name))


def _stateful_builder(state: _FakeQdrantState, ops: list):
    """Builder emulating the real R4 immutable staging contract: derive the
    stable target, resolve (create | verified read-only reuse), store the
    candidate payloads + dense vectors on create."""
    import scripts.ingest_corpus_v2 as ing
    from src.application.publish_state import resolve_staging_target

    def builder(chunks, qdrant_url, root, *, collection_name,
                corpus_version="", build_identity="", expected_identity=None):
        if not build_identity or expected_identity is None:
            raise ValueError(
                "build_identity/expected_identity contract violated")
        ops.append(("resolve", collection_name))
        target, mode, verified = resolve_staging_target(
            state, target_name=collection_name, expected=expected_identity,
            expected_vector_size=512, sparse_enabled=True,
            guarded_collections=ing._guarded_collections(state, Path(root)))
        if mode == "create":
            state.create_collection(target)
            payloads = ing._build_payloads(
                chunks, Path(root), corpus_version, build_identity)
            state.payloads[target] = payloads
            state.vectors[target] = [
                {"dense": [0.0] * 512} for _ in payloads]
            state.collections[target] = len(payloads)
            ops.append(("create", target))
        ops.append(("smoke", target))
        # reuse: the counts are the MEASURED ones from the verification
        # scroll, never the assumed expectation
        vector_count = verified["vector_count"] if verified else len(chunks)
        point_count = verified["point_count"] if verified else len(chunks)
        return {
            "collection": target, "mode": mode, "build_status": "ok",
            "qdrant_url": qdrant_url,
            "vector_count": vector_count, "point_count": point_count,
            "search_smoke": [{"query": q, "hit_count": 5,
                              "top_chunk_id": None, "top_file_name": None,
                              "top_score": 1.0, "top_text_head": None}
                             for q in FIXED_SMOKE_QUERIES],
        }
    return builder


class TestStagingImmutabilityE2E(unittest.TestCase):
    """R4 acceptance scenarios end-to-end through the real ingest()."""

    _ingest = TestIngestStateMachine._ingest

    def test_republish_rerun_without_publish_never_mutates_staged_collection(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            state = _FakeQdrantState()
            ops1: list = []
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=True, builder=_stateful_builder(state, ops1),
                                  admin=admin)
            staged = report["staging_collection"]
            self.assertIn(staged, state.collections)
            # re-run the SAME corpus version with publish=False
            ops2: list = []
            report2 = self._ingest(env, publish=False, builder=_stateful_builder(state, ops2))
            # identity-verified read-only reuse -- zero destructive ops
            self.assertEqual(report2["build"]["mode"], "reuse")
            self.assertEqual(report2["staging_collection"], staged)
            destructive = [op for op in ops2
                           if op[0] in ("delete", "create", "upsert")]
            self.assertEqual(destructive, [])
            # live state untouched by the re-run
            self.assertEqual(admin.calls, [("verify", staged),
                                           ("switch", "corpus_v2_live", staged)])
            self.assertEqual(
                json.loads(pointer.read_text(encoding="utf-8"))["corpus_version"],
                env.cv)

    def test_same_name_foreign_content_hard_fails_without_any_mutation(self):
        # acceptance matrix: a collection at the DERIVED name holding foreign
        # content must hard-fail -- never be deleted, recreated or upserted
        import scripts.ingest_corpus_v2 as ing
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            # the chunk manifest does not exist yet (ingest writes it);
            # derive the physical name from the immutable inputs directly
            from src.application.publish_state import (
                build_chunk_manifest,
                write_chunk_manifest,
            )
            from src.application.v2_chunker import chunk_document
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            chunks = [chunk for path in sorted(cv_dir.glob("*.canonical.json"))
                      for chunk in chunk_document(json.loads(
                          path.read_text(encoding="utf-8")))]
            write_chunk_manifest(env.root, env.cv, build_chunk_manifest(chunks))
            state = _FakeQdrantState()
            derived = ing.staging_build_collection_name(
                env.cv, ing._build_identity(env.root, env.cv))
            state.collections[derived] = 999
            state.payloads[derived] = [
                {"chunk_id": f"foreign_{n}", "text": "别的语料的内容",
                 "usage_policy": "answer_and_citation",
                 "source_sha256": "c" * 64, "file_name": "other.pdf",
                 "build_identity": "bi-000000000000000000000000"}
                for n in range(3)]
            ops: list = []
            report = self._ingest(env, publish=False,
                                  builder=_stateful_builder(state, ops))
            self.assertEqual(report["status"], "failed")
            self.assertIn("CollectionIdentityError",
                          report["build"]["error"])
            # foreign content preserved byte-for-byte; zero mutations
            self.assertEqual(len(state.payloads[derived]), 3)
            self.assertNotIn("delete", [op[0] for op in ops])
            self.assertNotIn("create", [op[0] for op in ops])
            self.assertNotIn("upsert", [op[0] for op in ops])

    def test_crash_before_manifest_rerun_does_not_create_second_collection(self):
        # acceptance matrix (R4): build done but manifest never landed ->
        # the rerun re-derives the SAME physical name and reuses read-only
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            self._ingest(env, publish=False, builder=_stateful_builder(state, []))
            staged = next(
                name for name in state.collections
                if name.startswith(f"corpus_v2__staging__{env.cv}__"))
            # simulate the crash: NOTHING of the stage chain survived
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            for name in ("chunk_manifest.json", "ingest_report.json",
                         "ingest_manifest.json", "publish_gate_record.json"):
                (cv_dir / name).unlink()
            ops2: list = []
            report2 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, ops2))
            self.assertEqual(report2["status"], "ok")
            # same derived name, reuse -- no second collection was created
            self.assertEqual(report2["staging_collection"], staged)
            self.assertEqual(report2["build"]["mode"], "reuse")
            self.assertEqual(
                [op for op in ops2 if op[0] in ("create", "delete", "upsert")],
                [])
            staging_names = [name for name in state.collections
                             if name.startswith("corpus_v2__staging__")]
            self.assertEqual(staging_names, [staged])

    def test_embedding_profile_change_is_never_reused(self):
        # acceptance matrix (R4): same chunks but a different embedding
        # profile -> a different build identity -> the old collection must
        # NOT be reused; the chain-bound target mismatches -> hard fail
        import scripts.ingest_corpus_v2 as ing
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            report1 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, []))
            staged = report1["staging_collection"]
            original = ing._embedding_profile
            ing._embedding_profile = lambda: {
                "model_id": "BAAI/bge-small-zh-v1.5",
                "model_revision": "rev-tampered",
                "dimension": 512}
            try:
                ops2: list = []
                report2 = self._ingest(env, publish=False,
                                       builder=_stateful_builder(state, ops2))
            finally:
                ing._embedding_profile = original
            self.assertEqual(report2["status"], "failed")
            self.assertIn("CollectionIdentityError",
                          report2["build"]["error"])
            # the old collection was never touched, no new collection created
            self.assertEqual(list(state.collections), [staged])
            self.assertEqual(
                [op for op in ops2 if op[0] in ("create", "delete", "upsert")],
                [])

    def test_payload_tamper_is_never_reused(self):
        # acceptance matrix (R4): stored text tampered -> the full-scroll
        # payload digest no longer matches -> read-only reuse is refused
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            report1 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, []))
            staged = report1["staging_collection"]
            state.payloads[staged][0]["text"] = "被篡改后的正文内容"
            ops2: list = []
            report2 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, ops2))
            self.assertEqual(report2["status"], "failed")
            self.assertIn("CollectionIdentityError",
                          report2["build"]["error"])
            # nothing was rewritten (the tampered point was NOT "repaired")
            self.assertEqual(state.payloads[staged][0]["text"],
                             "被篡改后的正文内容")
            self.assertEqual(
                [op for op in ops2 if op[0] in ("create", "delete", "upsert")],
                [])

    def test_evidence_identity_tamper_is_never_reused(self):
        # T06-R5: page, bbox, element membership and canonical identity are
        # part of the collection identity -- each tampering refuses reuse
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            report1 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, []))
            staged = report1["staging_collection"]
            pristine = [dict(p, source_spans=[dict(s) for s in p["source_spans"]],
                             element_ids=list(p["element_ids"]))
                        for p in state.payloads[staged]]
            mutations = [
                {"field": "source_spans[0].physical_page",
                 "apply": lambda p: p["source_spans"][0].__setitem__(
                     "physical_page", 9)},
                {"field": "source_spans[0].bbox",
                 "apply": lambda p: p["source_spans"][0].__setitem__(
                     "bbox", [60.0, 300.0, 500.0, 999.0])},
                {"field": "element_ids",
                 "apply": lambda p: p.__setitem__(
                     "element_ids", p["element_ids"] + ["el_extra"])},
                {"field": "canonical_id",
                 "apply": lambda p: p.__setitem__("canonical_id", "canon-x")},
                {"field": "canonical_content_id",
                 "apply": lambda p: p.__setitem__(
                     "canonical_content_id", "content-x")},
                {"field": "projection_version",
                 "apply": lambda p: p.__setitem__(
                     "projection_version", "v2-projection/9")},
            ]
            ops_all: list = []
            for mutation in mutations:
                # restore the pristine stream, apply ONE tampering
                state.payloads[staged] = [
                    dict(p, source_spans=[dict(s) for s in p["source_spans"]],
                         element_ids=list(p["element_ids"])) for p in pristine]
                for p in state.payloads[staged]:
                    mutation["apply"](p)
                ops: list = []
                ops_all.extend(ops)
                report = self._ingest(env, publish=False,
                                      builder=_stateful_builder(state, ops))
                self.assertEqual(report["status"], "failed",
                                 mutation["field"])
                self.assertIn("CollectionIdentityError",
                              report["build"]["error"], mutation["field"])
            # nothing was ever deleted/created/upserted across ALL attempts
            self.assertEqual(
                [op for op in ops_all
                 if op[0] in ("create", "delete", "upsert")], [])

    def test_point_vector_anomalies_fail_closed(self):
        # T06-R5: a stored point without its dense vector -- or with a wrong
        # dimension -- must refuse reuse (fail-closed, zero mutation)
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            report1 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, []))
            staged = report1["staging_collection"]
            ops: list = []
            state.vectors[staged] = [None] + state.vectors[staged][1:]
            report = self._ingest(env, publish=False,
                                  builder=_stateful_builder(state, ops))
            self.assertEqual(report["status"], "failed")
            self.assertIn("CollectionIdentityError", report["build"]["error"])
            self.assertEqual(
                [op for op in ops if op[0] in ("create", "delete", "upsert")],
                [])
            # wrong dense dimension is equally refused
            ops2: list = []
            state.vectors[staged] = [{"dense": [0.0] * 511}]
            report2 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, ops2))
            self.assertEqual(report2["status"], "failed")
            self.assertIn("CollectionIdentityError", report2["build"]["error"])
            self.assertEqual(
                [op for op in ops2 if op[0] in ("create", "delete", "upsert")],
                [])

    def test_legacy_base_staging_record_cannot_publish_with_noop_verifier(self):
        # T06-R5: a t06-2-era record (no build identity, base staging name)
        # must be refused -- even with a noop verifier
        from src.application.publish_state import PublishGateError, switch_pointer
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            report1 = self._ingest(env, publish=False,
                                   builder=_stateful_builder(state, []))
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            record = json.loads(
                (cv_dir / "publish_gate_record.json").read_text(encoding="utf-8"))
            record["schema_version"] = "t06-2"
            record.pop("build_identity", None)
            record["collection_name"] = f"corpus_v2__staging__{env.cv}"
            record_path = cv_dir / "publish_gate_record.json"
            _write_json(record_path, record)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            before = pointer.read_bytes()
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            with self.assertRaises(PublishGateError):
                switch_pointer(env.root, env.cv, alias_admin=admin,
                               verify_collection=lambda name: None)
            self.assertEqual(pointer.read_bytes(), before)
            self.assertEqual(admin.calls, [])

    def test_offline_failure_does_not_initialize_qdrant(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            # tamper the assembled canonical -> offline check fails
            canonical = (env.root / "data" / "canonical" / "v2" / env.cv
                         / f"{env.sha}.canonical.json")
            doc = json.loads(canonical.read_text(encoding="utf-8"))
            doc["elements"] = []
            canonical.write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            inits = []
            def admin_factory(url):
                inits.append(url)
                return _FakeAliasAdmin()
            calls = []
            def sentinel(*a, **k):
                calls.append(1)
                raise AssertionError("Qdrant must not be touched on offline failure")
            with self.assertRaises(Exception):
                self._ingest(env, publish=True, builder=sentinel,
                             admin_factory=admin_factory)
            self.assertEqual(inits, [])
            self.assertEqual(calls, [])


class TestRuntimeQueryEntry(unittest.TestCase):
    _ingest = TestIngestStateMachine._ingest

    def test_running_service_follows_alias_publish_and_rollback(self):
        from src.application.corpus_artifacts import resolve_query_collection
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            pointer = env.root / "data" / "registry" / "corpus-current.json"
            _write_json(pointer, {"corpus_version": "corpus-old",
                                  "status": "published"})
            # a "running service" resolves the entry ONCE at startup and
            # resolves every query through the server-side alias mapping
            alias_map = {"corpus_v2_live": "corpus_v2_old_target"}

            class AliasServerAdmin:
                def current_target(self, alias):
                    return alias_map.get(alias)
                def switch(self, alias, collection):
                    alias_map[alias] = collection
                def remove_alias(self, alias):
                    alias_map.pop(alias, None)
                def verify_payload_identity(self, collection_name, *, expected,
                                            expected_vector_size, sparse_enabled):
                    pass

            query_entry = resolve_query_collection(env.root)
            admin = AliasServerAdmin()

            report = self._ingest(env, publish=True, admin=admin)
            staged = report["staging_collection"]
            self.assertTrue(staged.startswith(
                f"corpus_v2__staging__{env.cv}__"))
            self.assertEqual(query_entry, "corpus_v2_live")
            current = json.loads(
                pointer.read_text(encoding="utf-8"))
            self.assertEqual(current["alias"], "corpus_v2_live")
            self.assertEqual(current["collection_name"], staged)
            self.assertEqual(current["build_identity"],
                             report["build_identity"])
            # the running service now sees the staged collection live
            self.assertEqual(alias_map[query_entry], staged)
            # rollback flips the alias back -- same entry, old target
            rollback_pointer(env.root, alias_admin=admin)
            self.assertEqual(query_entry, "corpus_v2_live")
            self.assertEqual(alias_map[query_entry], "corpus_v2_old_target")


# --------------------------------------------------------------------------- #
# QXT recovery: retired in-place patch -> override input of a unified build
# --------------------------------------------------------------------------- #

class TestRecoveryOverride(unittest.TestCase):
    def test_reparse_script_no_longer_patches_published_jsonl(self):
        source = (_REPO / "scripts" / "reparse_qxt489_p3.py").read_text(
            encoding="utf-8")
        # the retired in-place patch: rewrite of <corpus>-documents.jsonl
        self.assertNotIn("-documents.jsonl", source,
                         "reparse must not reference the published page-intermediate")
        self.assertNotIn(".replace(", source)

    def test_write_recovery_pages_stays_out_of_published_tree(self):
        from src.application.corpus_update import write_recovery_pages
        with _ScriptRoot() as env:
            pages = [{"physical_page": 1, "decision_status": "approved",
                      "text": "恢复后的页面", "elements": [], "regions": []}]
            record = write_recovery_pages(env.root, env.sha, pages)
            rec_dir = env.root / "data" / "canonical" / "recovery" / env.sha
            self.assertTrue((rec_dir / "0001.json").is_file())
            self.assertTrue(record["recovery_dir"].startswith("data/canonical/recovery"))
            # published page-intermediate untouched
            jsonl = env.root / "data" / "canonical" / f"{env.cv}-documents.jsonl"
            before = jsonl.read_bytes()
            self.assertEqual(jsonl.read_bytes(), before)

    def test_recovery_override_is_used_by_build(self):
        from src.application.corpus_update import (
            ContentAsset,
            CorpusUpdateService,
            write_recovery_pages,
        )

        class ExplodingExtractor:
            config_id = {"stub": True}
            requires_structure = False

            def extract(self, asset, physical_page):
                raise AssertionError("override page must prevent extraction")

        class CountingExtractor(ExplodingExtractor):
            calls = 0

            def extract(self, asset, physical_page):
                CountingExtractor.calls += 1
                return {
                    "physical_page": physical_page,
                    "page_index": physical_page - 1,
                    "display_page_label": None, "width": 1.0, "height": 1.0,
                    "rotation": 0, "extraction_route": "stub",
                    "parser_name": "stub", "parser_profile": "stub",
                    "decision_status": "approved", "decision_reasons": [],
                    "publishable": True, "text": "from extractor",
                    "elements": [], "regions": [], "tables": [],
                }

        asset = ContentAsset(
            sha256="f" * 64, canonical_rel_path="x.pdf",
            display_file_name="x.pdf", page_count=1,
            source_files=(), metadata={})
        page = {
            "physical_page": 1, "page_index": 0, "display_page_label": None,
            "width": 1.0, "height": 1.0, "rotation": 0,
            "extraction_route": "recovery", "parser_name": "recovery",
            "parser_profile": "recovery", "decision_status": "approved",
            "decision_reasons": ["recovery_override"], "publishable": True,
            "text": "from recovery", "elements": [], "regions": [], "tables": [],
        }
        with _ScriptRoot() as env:
            write_recovery_pages(env.root, asset.sha256, [page])
            svc = CorpusUpdateService(
                env.root, page_extractor=ExplodingExtractor(),
                recovery_dir=env.root / "data" / "canonical" / "recovery")
            recovered, reused = svc._page(asset, "cfg", 1, ExplodingExtractor())
            self.assertEqual(recovered["text"], "from recovery")
            # without the override dir the extractor is consulted
            svc2 = CorpusUpdateService(env.root, page_extractor=CountingExtractor())
            from_extractor, _ = svc2._page(asset, "cfg2", 1, CountingExtractor())
            self.assertEqual(from_extractor["text"], "from extractor")
            self.assertEqual(CountingExtractor.calls, 1)

    def test_recovery_override_yields_new_corpus_version(self):
        from src.application.corpus_update import (
            ContentAsset,
            CorpusUpdateService,
            write_recovery_pages,
        )

        class StubExtractor:
            config_id = {"stub": True}
            requires_structure = False

            def extract(self, asset, physical_page):
                return {
                    "physical_page": physical_page,
                    "page_index": physical_page - 1,
                    "display_page_label": None, "width": 1.0, "height": 1.0,
                    "rotation": 0, "extraction_route": "stub",
                    "parser_name": "stub", "parser_profile": "stub",
                    "decision_status": "approved", "decision_reasons": [],
                    "publishable": True, "text": "base extraction",
                    "elements": [], "regions": [], "tables": [],
                }

        asset = ContentAsset(
            sha256="e" * 64, canonical_rel_path="y.pdf",
            display_file_name="y.pdf", page_count=1,
            source_files=(), metadata={})
        with _ScriptRoot() as env:
            base = CorpusUpdateService(
                env.root, page_extractor=StubExtractor())._document(
                    asset, "cfg", StubExtractor())[0]
            page = dict(StubExtractor().extract(asset, 1), text="recovered text")
            write_recovery_pages(env.root, asset.sha256, [page])
            recovered = CorpusUpdateService(
                env.root, page_extractor=StubExtractor(),
                recovery_dir=env.root / "data" / "canonical" / "recovery"
            )._document(asset, "cfg", StubExtractor())[0]
            # the source sha256 stays (it is the file identity), but the page
            # content is the recovered one
            self.assertEqual(recovered["sha256"], base["sha256"])
            self.assertEqual(recovered["pages"][0]["text"], "recovered text")
            self.assertNotEqual(base["pages"][0]["text"], "recovered text")
            # the corpus fingerprint includes the documents -> new version
            self.assertNotEqual(
                json.dumps(base, sort_keys=True),
                json.dumps(recovered, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
