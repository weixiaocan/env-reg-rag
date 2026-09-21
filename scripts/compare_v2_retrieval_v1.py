#!/usr/bin/env python
"""V2 vs V1 retrieval comparison on the 12 fixed golden-set questions (plan 2.4).

Scope (plan,已定): only CECS758 in-scope cases are scored; the rest are marked
``out_of_scope`` and excluded from the metrics. In-scope is determined by
evidence_id -> document membership (the reliable signal): a case is in-scope iff
one of its required/acceptable evidence_ids maps to the CECS758 document
(``doc_f3b23a4668270f16`` / ``asset_f3b23a4668270f16``). Matching itself uses
``expected_facts`` fragment-substring hits on the top-k retrieved V2 chunks --
no evidence_id bridge is built for matching (plan §2.4).

V2 side: CECS758 V2 chunks -> bge-small-zh embeddings -> in-memory Qdrant
collection (hybrid RRF via a thin local subclass of ``QdrantRetrievalIndex``
that works around qdrant-client 1.19.0's local-mode ``Bm25Config`` limitation;
``src/retrieval/qdrant_index.py`` is not modified).

For each in-scope case: ``question`` -> embedder -> RRF search (top-k) -> check
whether any ``expected_facts`` fragment is a substring of any retrieved chunk's
text (hit -> pass).

Output: per-case pass/fail/out_of_scope + matched fragments, in-scope pass rate,
and the V1 baseline (RRF Recall@1 0.5455 / MRR 0.6515) for context. The plan
does not require V2 >= V1 (V2 covers 1/21 documents); gap reasons are recorded.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/compare_v2_retrieval_v1.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.canonical_assembly import CECS758_SHA  # noqa: E402
from src.application.v2_chunker import chunk_document  # noqa: E402

DEFAULT_CORPUS_VERSION = "corpus-37456321a968"
DEFAULT_SHA = CECS758_SHA
_GOLDEN = os.path.join(_REPO_ROOT, "data", "evaluation", "golden-set-v1.json")
_CHUNKS = os.path.join(_REPO_ROOT, "data", "retrieval", f"{DEFAULT_CORPUS_VERSION}-chunks.jsonl")
_EVIDENCE = os.path.join(_REPO_ROOT, "data", "evidence", f"{DEFAULT_CORPUS_VERSION}-evidence.jsonl")

# CECS758 document identity prefix (matches doc_<sha> and asset_<sha> forms).
CECS758_DOC_PREFIX = CECS758_SHA[:16]  # f3b23a4668270f16

_COLLECTION = "corpus_v2_cecs758_compare"
_TOP_K = 5

# V1 retrieval baseline (plan Context) -- for comparison context only.
V1_BASELINE = {"rrf_recall_at_1": 0.5455, "rrf_mrr": 0.6515}


# --------------------------------------------------------------------------- #
# Scope determination
# --------------------------------------------------------------------------- #

def _build_evidence_to_doc() -> dict[str, str]:
    """Map evidence_id -> document_version_id (or asset_id) from V1 artifacts."""
    mapping: dict[str, str] = {}
    # 1. retrieval chunks: primary_evidence_id -> document_version_id
    if os.path.isfile(_CHUNKS):
        with open(_CHUNKS, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                eid = o.get("primary_evidence_id")
                doc = o.get("document_version_id") or ""
                if eid and doc:
                    mapping[eid] = doc
                for eid2 in o.get("evidence_ids") or []:
                    mapping.setdefault(eid2, doc)
    # 2. evidence units: evidence_id -> asset_id
    if os.path.isfile(_EVIDENCE):
        with open(_EVIDENCE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                eid = o.get("evidence_id")
                asset = o.get("asset_id") or ""
                if eid and asset:
                    mapping.setdefault(eid, asset)
    return mapping


def _is_cecs758_doc(doc_id: str) -> bool:
    return bool(doc_id) and CECS758_DOC_PREFIX in doc_id


def _determine_scope(case: dict[str, Any], ev_map: dict[str, str]) -> tuple[str, str]:
    """Return (scope, reason). scope in {"in_scope", "out_of_scope"}."""
    eids = list(case.get("required_evidence_ids") or []) + list(
        case.get("acceptable_evidence_ids") or []
    )
    docs = {ev_map.get(eid, "") for eid in eids if eid}
    docs.discard("")
    cecs_docs = {d for d in docs if _is_cecs758_doc(d)}
    if cecs_docs:
        return "in_scope", f"evidence_id maps to CECS758 ({sorted(cecs_docs)[0]})"
    if eids and not docs:
        return "out_of_scope", "evidence_id not found in V1 artifacts (likely a different/older corpus build)"
    if docs:
        non_cecs = sorted(docs)
        return "out_of_scope", f"evidence_id maps to non-CECS758 doc ({non_cecs[0]})"
    # No evidence_ids at all (REAL-002 corpus-gap case).
    jurisdiction = (case.get("required_filters") or {}).get("jurisdiction", "")
    if jurisdiction:
        return "out_of_scope", f"corpus-gap / jurisdiction={jurisdiction} (no CECS758 evidence)"
    if case.get("expected_outcome") == "locate_source_only":
        return "out_of_scope", "locate_source_only (not a retrieve task)"
    return "out_of_scope", "no CECS758 evidence membership"


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", "", s)


def _fragments(expected: str) -> list[str]:
    """Short robust anchor fragments from an expected_fact string.

    Mirrors verify_v2_canonical_single._fragments_from_text: split on
    punctuation, keep 4-12 char segments, plus the first 8 / last 6 chars of the
    flattened string. Fragments survive V1 native OCR garbling + paraphrase.
    """
    text = expected or ""
    parts = [p for p in re.split(r"[，。；、：\s]+", text) if p]
    anchors: list[str] = []
    for p in parts:
        if 4 <= len(p) <= 12:
            anchors.append(p)
    flat = "".join(parts)
    if flat:
        anchors.append(flat[:8])
        anchors.append(flat[-6:])
    seen: set[str] = set()
    out: list[str] = []
    for a in anchors:
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out[:6]


def _fact_hit_in_text(fact: str, text_norm: str) -> str | None:
    """Return the matching fragment if any fragment of ``fact`` is in ``text``."""
    for frag in _fragments(fact):
        if len(frag) >= 4 and _norm(frag) in text_norm:
            return frag
    return None


# --------------------------------------------------------------------------- #
# Local Qdrant index (thin subclass; qdrant_index.py untouched)
# --------------------------------------------------------------------------- #

def _build_local_index(chunks: list, embedder: Any):
    from qdrant_client import QdrantClient  # noqa: E402

    from src.retrieval.qdrant_index import (  # noqa: E402
        HybridQuery,
        RetrievalHit,
        QdrantRetrievalIndex,
    )

    class _LocalQdrantIndex(QdrantRetrievalIndex):
        @staticmethod
        def _bm25_options():  # type: ignore[override]
            # qdrant-client 1.19.0 local-mode cannot unpack Bm25Config; None
            # restores local hybrid (RRF) retrieval with default bm25 settings.
            return None

        @staticmethod
        def _to_hit(point: Any, *, score: float) -> "RetrievalHit":  # type: ignore[override]
            payload = point.payload or {}
            section_path = payload.get("section_path") or []
            heading_path = [
                f"{e.get('label','')} {e.get('title') or ''}".strip()
                for e in section_path
            ]
            jurisdictions = payload.get("jurisdictions") or []
            return RetrievalHit(
                chunk_id=str(payload.get("chunk_id", "")),
                text=str(payload.get("text", "")),
                primary_evidence_id="",  # V2 traces via element_ids
                physical_pages=list(payload.get("physical_pages", [])),
                usage_policy="answer_and_citation",
                score=score,
                file_name=str(payload.get("file_name", "")),
                heading_path=heading_path,
                document_kind=str(payload.get("document_kind", "unknown")),
                jurisdiction=jurisdictions[0] if jurisdictions else "unknown",
                effective_status=str(payload.get("effective_status", "unknown")),
            )

    payloads = [c.to_qdrant_payload() for c in chunks]
    vectors = embedder.embed_documents([c.text for c in chunks])
    client = QdrantClient(":memory:")
    index = _LocalQdrantIndex(
        client=client,
        collection_name=_COLLECTION,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    index.build(payloads, vectors)
    return index, HybridQuery


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def compare(sha: str = DEFAULT_SHA, corpus_version: str = DEFAULT_CORPUS_VERSION) -> dict[str, Any]:
    canonical_path = os.path.join(
        _REPO_ROOT, "data", "canonical", "v2", corpus_version, f"{sha}.canonical.json"
    )
    with open(canonical_path, "r", encoding="utf-8") as fh:
        canonical = json.load(fh)
    chunks = chunk_document(canonical)

    with open(_GOLDEN, "r", encoding="utf-8") as fh:
        golden = json.load(fh)
    cases = golden["cases"]

    ev_map = _build_evidence_to_doc()

    from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index, HybridQuery = _build_local_index(chunks, embedder)

    per_case: list[dict[str, Any]] = []
    in_scope_total = 0
    in_scope_pass = 0
    for case in cases:
        scope, reason = _determine_scope(case, ev_map)
        entry: dict[str, Any] = {
            "case_id": case["case_id"],
            "question": case["question"],
            "query_type": case.get("query_type"),
            "expected_outcome": case.get("expected_outcome"),
            "scope": scope,
            "scope_reason": reason,
            "expected_facts": case.get("expected_facts") or [],
        }
        if scope != "in_scope":
            entry["status"] = "out_of_scope"
            per_case.append(entry)
            continue

        in_scope_total += 1
        question = case["question"]
        dense = embedder.embed_query(question)
        hits = index.search(
            HybridQuery(text=question, dense_vector=dense),
            mode="rrf",
            limit=_TOP_K,
        )
        # Normalize retrieved text once.
        hit_texts_norm = [_norm(h.text) for h in hits]

        matched_facts: list[dict[str, Any]] = []
        all_facts_hit = True
        for fact in case.get("expected_facts") or []:
            hit_frag = None
            hit_chunk = None
            for h, htext in zip(hits, hit_texts_norm):
                frag = _fact_hit_in_text(fact, htext)
                if frag:
                    hit_frag = frag
                    hit_chunk = h.chunk_id
                    break
            matched_facts.append({
                "fact": fact,
                "hit": hit_frag is not None,
                "fragment": hit_frag,
                "chunk_id": hit_chunk,
            })
            if hit_frag is None:
                all_facts_hit = False

        # Lenient pass: at least one expected_fact fragment hit in top-k.
        any_hit = any(m["hit"] for m in matched_facts)
        entry["status"] = "pass" if any_hit else "fail"
        entry["all_facts_hit"] = all_facts_hit
        entry["matched_facts"] = matched_facts
        entry["top_k"] = [
            {"chunk_id": h.chunk_id, "score": round(h.score, 4),
             "text_head": h.text[:70]}
            for h in hits
        ]
        if any_hit:
            in_scope_pass += 1
        per_case.append(entry)

    pass_rate = round(in_scope_pass / in_scope_total, 4) if in_scope_total else None
    report = {
        "sha": sha,
        "corpus_version": corpus_version,
        "v2_chunk_count": len(chunks),
        "v2_collection": _COLLECTION,
        "retrieval_mode": "rrf (dense bge-small-zh + bm25, local in-memory)",
        "top_k": _TOP_K,
        "in_scope_cases": in_scope_total,
        "in_scope_pass": in_scope_pass,
        "in_scope_pass_rate": pass_rate,
        "v1_baseline": V1_BASELINE,
        "cases": per_case,
    }
    _write_report(report, corpus_version, sha)
    return report


def _write_report(report: dict[str, Any], corpus_version: str, sha: str) -> str:
    out_dir = os.path.join(_REPO_ROOT, "data", "canonical", "v2", corpus_version)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{sha}.retrieval_compare.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    return out_path


def _print_report(report: dict[str, Any]) -> None:
    print("=" * 76)
    print("V2 vs V1 Retrieval Comparison (plan 2.4)")
    print("=" * 76)
    print(f"  sha                : {report['sha']}")
    print(f"  v2_chunk_count     : {report['v2_chunk_count']}")
    print(f"  retrieval_mode     : {report['retrieval_mode']}")
    print(f"  top_k              : {report['top_k']}")
    print(f"  in_scope_cases     : {report['in_scope_cases']}")
    print(f"  in_scope_pass      : {report['in_scope_pass']}")
    pr = report["in_scope_pass_rate"]
    print(f"  in_scope_pass_rate : {pr if pr is not None else 'n/a'}")
    v1 = report["v1_baseline"]
    print(f"  v1_baseline        : RRF Recall@1={v1['rrf_recall_at_1']} MRR={v1['rrf_mrr']}")
    print()
    print("-" * 76)
    print("Per-case results")
    print("-" * 76)
    for c in report["cases"]:
        tag = c["scope"]
        status = c.get("status", "?")
        print(f"  {c['case_id']:<9} [{tag:<11}] {status:<11} q={c['question'][:34]!r}")
        if tag == "in_scope":
            for m in c.get("matched_facts", []):
                mark = "HIT " if m["hit"] else "miss"
                print(f"      {mark} fact={m['fact'][:30]!r} frag={m.get('fragment')!r}")
            for i, h in enumerate(c.get("top_k", [])[:2]):
                print(f"      top{i+1} score={h['score']} {h['text_head']!r}")
        else:
            print(f"      reason: {c['scope_reason']}")
    print("=" * 76)
    print("NOTE: V2 covers 1/21 documents (CECS758 only). Most golden cases are")
    print("out_of_scope by document membership. Full 12-case comparison is Phase 4.")
    print("=" * 76)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", default=DEFAULT_SHA)
    parser.add_argument("--corpus-version", default=DEFAULT_CORPUS_VERSION)
    args = parser.parse_args(argv)
    report = compare(args.sha, args.corpus_version)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
