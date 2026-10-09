"""Mint a T09 assembly-stage derived corpus candidate (read-only on the base).

The T09 formula/explanation association changes ASSEMBLY + PROJECTION only:
the page-intermediate (PI) is byte-identical to the base candidate's, so
``corpus_update.build`` cannot derive a new corpus id for it (extraction
config unchanged -> same fingerprint). This script mints the new immutable
candidate id deterministically and stages the inputs assemble_corpus_v2
needs, with create-only writes throughout:

    new_id = "corpus-" + sha256(stable_json({
        "assembly_profile": "t09-formula-binding-2",
        "base_corpus_version": <base>,
        "base_pi_sha256": <base PI sha256>,
    }))[:12]

ZERO-WRITE preflight (any missing/incorrect input aborts BEFORE the first
write): registry manifest must exist and MUST bind
``canonical_documents_sha256``; the PI on disk must exist and match it; a
bound ``structure_audit`` file must exist and match
``structure_audit_sha256`` (which itself must be present).

Writes (ALL create-only -- absent -> atomic write, byte-identical -> reuse,
different -> hard error):
  * ``data/canonical/<new>-documents.jsonl``      (byte-identical PI copy)
  * ``data/canonical/<new>-structure-audit.json`` (byte-identical audit copy)
  * ``data/registry/<new>.json``                  (derived registry manifest,
    ``status: ready``, plus an explicit ``assembly_provenance`` block)
  * ``--write-pointer``: ``data/registry/corpus-candidate.json`` (the
    sanctioned CANDIDATE pointer only -- corpus-current.json and Qdrant are
    NEVER touched by this script)

Existing candidates are NEVER overwritten: a changed assembly semantics
mints a new version (new assembly profile -> new id) and leaves the old
candidate directory untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

ASSEMBLY_PROFILE = "t09-formula-binding-7"


def _stable_json_sha(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _create_only_write(path: Path, data: bytes, *, what: str) -> str:
    """Absent -> atomic write; byte-identical -> reuse; different -> error."""
    if path.exists():
        existing = path.read_bytes()
        if existing != data:
            raise SystemExit(
                f"[mint] REFUSING to overwrite {what} at {path}: existing "
                "bytes differ (create-only invariant)")
        print(f"[mint] reuse identical {what}: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        print(f"[mint] wrote {what}: {path}")
    return _sha256_file(path)


def _preflight(root: Path, manifest: dict, registry_path: Path) -> str:
    """Verify every input BEFORE any write; return the base PI sha256."""
    pi_rel = manifest.get("canonical_documents")
    if not pi_rel:
        raise SystemExit(
            f"[mint] ZERO-WRITE PREFLIGHT FAILED: registry manifest "
            f"{registry_path} does not bind canonical_documents (no PI path)")
    pi_path = root / pi_rel
    if not pi_path.is_file():
        raise SystemExit(
            f"[mint] ZERO-WRITE PREFLIGHT FAILED: base page-intermediate "
            f"missing: {pi_path}")
    pi_sha = _sha256_file(pi_path)
    expected = manifest.get("canonical_documents_sha256")
    if not expected:
        raise SystemExit(
            f"[mint] ZERO-WRITE PREFLIGHT FAILED: registry manifest "
            f"{registry_path} does not bind canonical_documents_sha256 -- "
            "an unhashed page-intermediate can never be minted from")
    if pi_sha != expected:
        raise SystemExit(
            "[mint] ZERO-WRITE PREFLIGHT FAILED: base page-intermediate on "
            f"disk ({pi_sha}) does not match the manifest-bound hash "
            f"({expected}) -- aborting before any write")
    audit_rel = manifest.get("structure_audit")
    if audit_rel:
        audit_path = root / audit_rel
        if not audit_path.is_file():
            raise SystemExit(
                "[mint] ZERO-WRITE PREFLIGHT FAILED: structure_audit bound "
                f"but missing on disk: {audit_path}")
        audit_expected = manifest.get("structure_audit_sha256")
        if not audit_expected:
            raise SystemExit(
                "[mint] ZERO-WRITE PREFLIGHT FAILED: registry manifest binds "
                "structure_audit without structure_audit_sha256")
        audit_sha = _sha256_file(audit_path)
        if audit_sha != audit_expected:
            raise SystemExit(
                "[mint] ZERO-WRITE PREFLIGHT FAILED: structure audit on "
                f"disk ({audit_sha}) does not match the manifest-bound hash "
                f"({audit_expected})")
    else:
        raise SystemExit(
            "[mint] ZERO-WRITE PREFLIGHT FAILED: registry manifest binds no "
            "structure_audit -- the audit input is missing; refusing to "
            "mint from an unaudited base")
    return pi_sha


def main(argv: list[str] | None = None, root: str | Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-corpus-version", required=True,
                        help="base candidate corpus version to derive from")
    parser.add_argument("--write-pointer", action="store_true",
                        help="after staging, point corpus-candidate.json at "
                             "the new candidate (candidate pointer ONLY)")
    parser.add_argument("--assembly-profile", default=ASSEMBLY_PROFILE,
                        help="deterministic assembly semantics identifier; changing it mints a new id")
    parser.add_argument("--note", default=None,
                        help="deterministic provenance note for the derived candidate")
    parser.add_argument("--root", default=None,
                        help="project root override (tests only)")
    args = parser.parse_args(argv)

    base = args.base_corpus_version
    base_root = Path(root) if root is not None else _REPO_ROOT
    registry_path = base_root / "data" / "registry" / f"{base}.json"
    if not registry_path.is_file():
        raise SystemExit(f"[mint] base registry manifest missing: {registry_path}")
    manifest = json.loads(registry_path.read_text(encoding="utf-8"))

    # Zero-write preflight: every input verified before the first write.
    pi_sha = _preflight(base_root, manifest, registry_path)

    new_id = "corpus-" + _stable_json_sha({
        "assembly_profile": args.assembly_profile,
        "base_corpus_version": base,
        "base_pi_sha256": pi_sha,
    })[:12]
    print(f"[mint] base: {base} (pi sha256 {pi_sha[:16]}...)")
    print(f"[mint] new corpus version: {new_id}")

    # 1. Byte-identical PI copy (create-only).
    new_pi_rel = f"data/canonical/{new_id}-documents.jsonl"
    _create_only_write(base_root / new_pi_rel,
                       (base_root / manifest["canonical_documents"]).read_bytes(),
                       what="page-intermediate copy")

    # 2. Byte-identical structure-audit copy (preflight verified it).
    new_audit_rel = manifest.get("structure_audit")
    if new_audit_rel:
        new_audit_rel = f"data/canonical/{new_id}-structure-audit.json"
        _create_only_write(base_root / new_audit_rel,
                           (base_root / manifest["structure_audit"]).read_bytes(),
                           what="structure-audit copy")

    # 3. Derived registry manifest (status ready; explicit assembly provenance).
    new_manifest = dict(manifest)
    new_manifest["corpus_version"] = new_id
    new_manifest["canonical_documents"] = new_pi_rel
    if new_audit_rel:
        new_manifest["structure_audit"] = new_audit_rel
    new_manifest["assembly_provenance"] = {
        "schema_version": "t09-assembly-provenance-1",
        "assembly_profile": args.assembly_profile,
        "derived_from": base,
        "base_pi_sha256": pi_sha,
        "base_manifest_sha256": hashlib.sha256(
            registry_path.read_bytes()).hexdigest(),
        "pi_byte_identical": True,
        # NOTE: deliberately NO wall-clock timestamp here -- the derived
        # manifest must be byte-deterministic so a re-run reuses it under the
        # create-only invariant instead of colliding with itself.
        "minted_by": "scripts/mint_assembly_candidate.py",
        "note": args.note or ("T09 formula/explanation association (closure round 5: "
                 "occurrence-aware chunk identity -- every formula-binding "
                 "chunk declares the exact element-relative source char "
                 "ranges it carries (metadata source_occurrence, hashed "
                 "into compute_chunk_id), so byte-identical texts at "
                 "DIFFERENT source positions are distinct, trackable, "
                 "deterministically-unique occurrences (u6b's 8 identical "
                 "explanation lines retained 8x; real duplicate rows of the "
                 "empty-grid table 48429f37 e000261 distinguished from "
                 "splitter overlap windows by position, never by text "
                 "sets); the offset-exact window packer replaces the "
                 "recursive splitter on the formula-binding projection so "
                 "declared ranges are exact, while the legacy projection "
                 "path stays verbatim (published-corpus re-projection "
                 "byte-stable); the same chunk_id re-emitted with a "
                 "DIFFERENT policy/quality/reasons/occurrence is a hard "
                 "error in _emit_chunk AND in the global "
                 "assert_unique_chunk_ids gate -- never first-wins. "
                 "Round-4/-3 semantics retained: deterministic dedupe of "
                 "identical projections, global uniqueness gate, "
                 "mixed-stop conservative truncation, per-shard span "
                 "provenance, central <=512-token emission gate, anchor "
                 "overflow short locator records -- assembly + chunk "
                 "projection change on a byte-identical page-intermediate"),
    }
    new_manifest_bytes = json.dumps(
        new_manifest, ensure_ascii=False, indent=2,
        sort_keys=True).encode("utf-8") + b"\n"
    _create_only_write(base_root / "data" / "registry" / f"{new_id}.json",
                       new_manifest_bytes, what="registry manifest")

    # 4. Candidate pointer (explicit opt-in; NEVER corpus-current.json). The
    # pointer is a mutable by-design reference -- it is the ONLY file this
    # script overwrites.
    if args.write_pointer:
        pointer_path = base_root / "data" / "registry" / "corpus-candidate.json"
        pointer = {
            "corpus_version": new_id,
            "status": "candidate",
            "manifest": f"data/registry/{new_id}.json",
            "manifest_sha256": hashlib.sha256(new_manifest_bytes).hexdigest(),
        }
        pointer_bytes = json.dumps(
            pointer, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        path = pointer_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(pointer_bytes)
        os.replace(tmp, path)
        print("[mint] corpus-candidate.json -> "
              f"{new_id} (corpus-current.json untouched)")

    print(f"[mint] done: {new_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
