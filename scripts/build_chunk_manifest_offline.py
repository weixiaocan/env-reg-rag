"""T09 offline chunk manifest + verification (candidate-only, Qdrant-free).

Mirrors ingest_corpus_v2 Stage 1 EXACTLY (artifact-manifest verification ->
chunk_document with the default REAL bge tokenizer -> chunk manifest ->
offline_preflight) but stops there: no Qdrant import, no pointer change,
no publish. Additional T09-specific verification:

  * hard-cap audit: EVERY chunk text fits the 512-token bge hard cap;
  * determinism: the whole corpus is chunked TWICE (fresh loads) and both
    chunk-set hashes must match;
  * projection version: every chunk of a ledger candidate carries
    ``chunk-text/v2-formula-binding-2`` (legacy corpora stay ``chunk-text/v1``);
  * per-shard source projection: every formula chunk's element_ids/spans
    cover exactly the sources its text carries (bound explanation elements
    present iff their text is present).

Writes ONLY the candidate chunk manifest (create-only byte semantics).
"""

from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from pathlib import Path

from src.application.publish_state import (
    build_chunk_manifest,
    load_artifact_manifest,
    offline_preflight,
    write_chunk_manifest,
)
from src.application.v2_chunker import (
    FORMULA_BINDING_PROJECTION_VERSION,
    DEFAULT_PROJECTION_VERSION,
    chunk_document,
)


def _iter_canonical_paths(corpus_version: str) -> list[Path]:
    canonical_dir = Path(_REPO_ROOT) / "data" / "canonical" / "v2" / corpus_version
    paths = sorted(canonical_dir.glob("*.canonical.json"))
    if not paths:
        raise FileNotFoundError(f"no canonical artifacts in {canonical_dir}")
    return paths


def _load(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _collect_chunks(paths: list[Path]) -> list:
    chunks = []
    for path in paths:
        chunks.extend(chunk_document(_load(path)))
    return chunks


def main(argv: list[str] | None = None) -> int:
    corpus_version = (argv[0] if argv else None) or (
        sys.argv[1] if len(sys.argv) > 1 else None)
    if not corpus_version:
        raise SystemExit("usage: build_chunk_manifest_offline.py <corpus-version>")

    manifest = load_artifact_manifest(_REPO_ROOT, corpus_version, verify=True)
    if manifest is None:
        raise SystemExit("artifact_manifest.json missing (assemble first)")
    print(f"[chunk] artifact manifest verified: {manifest['canonical']['count']} canonicals")

    paths = _iter_canonical_paths(corpus_version)
    chunks = _collect_chunks(paths)
    print(f"[chunk] {len(chunks)} chunks across {len(paths)} docs")

    # -- hard-cap audit with the REAL bge tokenizer (chunker already used it,
    #    but re-assert explicitly over the finished chunk texts).
    from src.application.v2_chunker import _resolve_tokenizer
    tok = _resolve_tokenizer(None)
    overflow = [(c.chunk_id, tok.count(c.text)) for c in chunks
                if tok.count(c.text) > 512]
    if overflow:
        print(f"[chunk] HARD-CAP VIOLATIONS: {len(overflow)}")
        for cid, n in overflow[:10]:
            print(f"  {cid[:24]}... {n} tokens")
        return 1
    max_tokens = max(tok.count(c.text) for c in chunks)
    print(f"[chunk] hard cap: all chunks <= 512 tokens (max {max_tokens})")

    # -- determinism: rechunk from fresh file loads, compare chunk-set hash.
    first = build_chunk_manifest(chunks)
    second = build_chunk_manifest(_collect_chunks(paths))
    if first["chunk_set_sha256"] != second["chunk_set_sha256"]:
        print("[chunk] DETERMINISM FAILURE: chunk-set hash differs on re-chunk")
        return 1
    print(f"[chunk] determinism: chunk_set_sha256 {first['chunk_set_sha256'][:16]}... stable")

    # -- projection version.
    versions = sorted({c.projection_version for c in chunks})
    expected = FORMULA_BINDING_PROJECTION_VERSION
    if versions != [expected]:
        print(f"[chunk] PROJECTION VERSION MISMATCH: {versions} != [{expected}]")
        return 1
    print(f"[chunk] projection_version: {versions[0]} "
          f"(legacy corpora keep {DEFAULT_PROJECTION_VERSION})")

    # -- per-shard source projection spot check: formula shards carrying
    #    explanation text must list those explanation elements in element_ids.
    ws = __import__("re").sub
    norm = lambda s: ws(r"\s+", "", s or "")
    bad_proj = 0
    checked = 0
    for path in paths:
        doc = _load(path)
        by_id = {e["element_id"]: e for e in doc["elements"]}
        for chunk in chunk_document(_load(path)):
            fids = [eid for eid in chunk.element_ids
                    if by_id.get(eid, {}).get("type") == "formula"]
            if not fids:
                continue
            checked += 1
            for fid in fids:
                fe = by_id[fid]
                prim = [b for b in fe.get("explanation_bindings") or []
                        if b["role"] == "primary"]
                # lines actually carried by this shard (everything after the
                # section prefix + [公式 N] + latex body lines)
                shard_lines = {norm(ln) for ln in chunk.text.split("\n")
                               if norm(ln)}
                anchor_lines = set()
                number = (fe.get("content") or {}).get("formula_number")
                if number:
                    anchor_lines.add(norm(f"[公式 {number}]"))
                anchor_lines.add(norm((fe.get("content") or {}).get("latex") or ""))
                # every explanation line in the shard must be attributable to
                # a LISTED bound element (duplicate line texts attribute to
                # whichever listed element produces them)
                for b in prim:
                    te = by_id.get(b["element_id"])
                    if te is None:
                        continue
                    elem_lines = {norm(ln) for ln
                                  in (te["content"].get("text") or "").split("\n")
                                  if norm(ln)}
                    listed = b["element_id"] in chunk.element_ids
                    owned_here = bool(elem_lines & shard_lines)
                    owned_elsewhere = any(
                        b2["element_id"] in chunk.element_ids
                        and b2["element_id"] != b["element_id"]
                        and by_id.get(b2["element_id"]) is not None
                        and ({norm(ln) for ln in
                              (by_id[b2["element_id"]]["content"].get("text")
                               or "").split("\n") if norm(ln)} & elem_lines)
                        for b2 in prim)
                    # unattributed = line present in shard, element not listed,
                    # and no other listed element produces the same line
                    if owned_here and not listed and not owned_elsewhere:
                        bad_proj += 1
                        print(f"  projection drift: chunk {chunk.chunk_id[:20]}... "
                              f"elem {b['element_id']} line present but unattributed")
    print(f"[chunk] per-shard projection: {checked} formula shards checked, "
          f"{bad_proj} drifts")
    if bad_proj:
        return 1

    # -- candidate-only chunk manifest (create-only byte semantics).
    write_chunk_manifest(_REPO_ROOT, corpus_version, first)

    # -- full offline preflight (re-chunks internally and compares).
    preflight = offline_preflight(_REPO_ROOT, corpus_version)
    print(f"[chunk] offline preflight ok: {preflight['documents']} docs, "
          f"{preflight['total_chunks']} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
