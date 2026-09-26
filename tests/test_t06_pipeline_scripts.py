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

def _fake_builder(total: int, *, smoke_hits: int = 5):
    """Deterministic staging build: records the target collection, returns
    bytes-stable report data (no timings) so re-runs stay idempotent."""
    def builder(chunks, qdrant_url, root, *, collection_name, corpus_version=""):
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


class TestIngestStateMachine(unittest.TestCase):
    def _ingest(self, env: _ScriptRoot, *, publish: bool, builder=None,
                admin=None, admin_factory=None):
        import scripts.ingest_corpus_v2 as ing
        return ing.ingest(
            qdrant_url="http://sentinel.invalid:6333",
            corpus_version=env.cv, publish=publish,
            root=env.root,
            _builder=builder or _fake_builder(
                len(getattr(env, "_chunks", [])) or 1),
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
                        corpus_version=""):
                seen["collection"] = collection_name
                return _fake_builder(len(chunks))(chunks, qdrant_url, root,
                                                  collection_name=collection_name)
            admin = _FakeAliasAdmin(initial="corpus_v2_old_target")
            report = self._ingest(env, publish=False, builder=builder,
                                  admin=admin)
            self.assertEqual(report["status"], "ok")
            # a versioned staging collection, never the live one
            self.assertEqual(seen["collection"],
                             f"corpus_v2__staging__{env.cv}")
            self.assertNotEqual(seen["collection"], "corpus_v2")
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
            current = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(current["corpus_version"], env.cv)
            # the pointer now names the VERSIONED staging collection
            self.assertEqual(current["collection_name"],
                             f"corpus_v2__staging__{env.cv}")
            # the alias was switched to the staged collection
            self.assertEqual(admin.calls, [("switch", "corpus_v2_live",
                                            f"corpus_v2__staging__{env.cv}")])
            records = list((env.root / "data" / "registry" / "rollback").glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            self.assertEqual(record["previous_pointer"]["corpus_version"], "corpus-old")
            self.assertEqual(record["previous_collection"], "corpus_v2_old_target")
            # the parent-hash chain binds the ingest report
            cv_dir = env.root / "data" / "canonical" / "v2" / env.cv
            ingest_manifest = json.loads(
                (cv_dir / "ingest_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(ingest_manifest["ingest_report_sha256"],
                             sha256_file(cv_dir / "ingest_report.json"))
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
                        corpus_version=""):
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
                        corpus_version=""):
                return _fake_builder(len(chunks), smoke_hits=0)(
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
    """Duck-typed collection registry backing resolve_staging_target."""

    def __init__(self, collections=None, vector_size=512):
        self.collections = dict(collections or {})
        self.vector_size = vector_size
        self.ops: list[tuple] = []

    def collection_exists(self, name):
        return name in self.collections

    def get_collection(self, name):
        info = type("Info", (), {})()
        info.points_count = self.collections[name]

        class _Vectors:
            dense = type("Dense", (), {"size": self.vector_size})()
        info.config = type("Config", (), {"params": _Vectors()})()
        return info

    def delete_collection(self, name):
        self.ops.append(("delete", name))
        self.collections.pop(name, None)

    def create_collection(self, name):
        self.ops.append(("create", name))
        self.collections[name] = 0

    def upsert(self, name, points):
        self.ops.append(("upsert", name))


def _stateful_builder(state: _FakeQdrantState, ops: list):
    """Builder emulating the real immutable staging contract."""
    from src.application.publish_state import resolve_staging_target

    def builder(chunks, qdrant_url, root, *, collection_name, corpus_version=""):
        ops.append(("resolve", collection_name))
        target, mode = resolve_staging_target(
            state, corpus_version, chunk_count=len(chunks), vector_size=512)
        if mode == "create":
            state.create_collection(target)
            state.collections[target] = len(chunks)
            ops.append(("create", target))
        ops.append(("smoke", target))
        return {
            "collection": target, "mode": mode, "build_status": "ok",
            "qdrant_url": qdrant_url,
            "vector_count": len(chunks), "point_count": len(chunks),
            "search_smoke": [{"query": q, "hit_count": 5,
                              "top_chunk_id": None, "top_file_name": None,
                              "top_score": 1.0, "top_text_head": None}
                             for q in FIXED_SMOKE_QUERIES],
        }
    return builder


class TestStagingImmutabilityE2E(unittest.TestCase):
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
            destructive = [op for op in ops2
                           if op[0] in ("delete", "create", "upsert")]
            self.assertEqual(destructive, [])
            # live state untouched by the re-run
            self.assertEqual(admin.calls, [("switch", "corpus_v2_live", staged)])
            self.assertEqual(
                json.loads(pointer.read_text(encoding="utf-8"))["corpus_version"],
                env.cv)

    def test_existing_staging_is_never_overwritten(self):
        with _ScriptRoot() as env:
            self.assertEqual(_assemble(env.root, env.cv), 0)
            state = _FakeQdrantState()
            staged = f"corpus_v2__staging__{env.cv}"
            state.collections[staged] = 999  # foreign content
            ops: list = []
            report = self._ingest(env, publish=False, builder=_stateful_builder(state, ops))
            # fresh immutable build identity, old collection untouched
            self.assertNotEqual(report["build"]["collection"], staged)
            self.assertIn(staged, state.collections)
            self.assertNotIn("delete", [op[0] for op in ops])
            self.assertEqual(ops.count(("create", staged)), 0)

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

            query_entry = resolve_query_collection(env.root)
            admin = AliasServerAdmin()

            self._ingest(env, publish=True, admin=admin)
            self.assertEqual(query_entry, "corpus_v2_live")
            current = json.loads(
                pointer.read_text(encoding="utf-8"))
            self.assertEqual(current["alias"], "corpus_v2_live")
            self.assertEqual(current["collection_name"],
                             f"corpus_v2__staging__{env.cv}")
            # the running service now sees the staged collection live
            self.assertEqual(alias_map[query_entry],
                             f"corpus_v2__staging__{env.cv}")
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
