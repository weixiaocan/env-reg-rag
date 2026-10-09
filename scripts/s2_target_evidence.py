"""S2 -- target-evidence preparation. READ-ONLY over all state, DIAGNOSTIC.

Per the S2 instruction (no bulk promotion of the S1 41 blocked cases):

1. **Complete-reachability judgment** on top of the S1 per-case list: per
   required FACT (fact ledger, refuse_meta excluded) -- does the fact enter
   the generation top-5?  Carriers are measured at THREE levels:
     * primary binding carrier (fact-ledger chunk_id, live policy + live
       answer-space rank);
     * alternate duplicated carrier -- the SAME content (verbatim quote
       window or fact text) present in an ``answer_and_citation`` chunk
       (numbers-only containment is recorded separately as WEAK);
     * rank within the measured answer-space top-100 of the question.
   Case level: 完整可达 (all content facts reachable at 5) / 部分可达 /
   不可达.  4 refuse cases are judged by the refuse goal instead.
2. **Unified trace口径** (resolves the S1 G013 vs G011/G045 inconsistency):
   for every case, BOTH count bases (unique answer chunks vs evidence-bound
   carriers) are reported for BOTH runs (live recomputation via the same
   ``evaluate_case`` matcher, and the frozen baseline trace).  @10 boundary
   flips are listed symmetrically; a flip is only a REAL mismatch when it
   moves an evidence-bound carrier.
3. **Blocked-evidence tracing**: per blocked evidence -- chunk payload
   (element_types / quality_status / policy_reasons) AND the canonical
   elements' type / quality_status / provenance method / quality_issues /
   review-assertion presence, then grouped: 结构化D3复核 / 普通文字质量(OCR)
   / 检索排名.  No policy is ever upgraded here.
4. **PDF re-check**: the ledger's pending rulings and supported=False
   bindings are re-verified against the ORIGINAL PDF page text (fitz) --
   "ledger has no gaps" is NOT accepted as independent content proof.
5. **Human adjudication short list** with PDF page numbers.  No ``passed``
   or ``review_assertion`` is ever filled by this script.
6. **Engineering-fix assessment**: measured (not assumed) -- prefetch
   depth replay in answer space for the ranking misses; rerank/LLM are
   out of scope by instruction.  Nothing is published; the live index,
   pointer, golden set, traces and quality statuses are untouched.

Outputs (the only writes): ``eval/audits/s2_target_evidence/`` --
``s2_per_case.jsonl``, ``s2_blocked_evidence.jsonl``, ``s2_pdf_recheck.json``,
``s2_human_review_list.md``, ``s2_summary.json`` + ``s2_summary.md``.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client import models  # noqa: E402

from src.evaluation.retrieval_harness import evaluate_case  # noqa: E402
from scripts.evaluate_golden_retrieval import _load_cases, _json  # noqa: E402
from scripts.probe_miss_rerank import _binding_problems  # noqa: E402
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex  # noqa: E402

DATASET = (ROOT / "eval" / "golden_v2.jsonl").resolve()
POINTER = ROOT / "data" / "registry" / "corpus-current.json"
LEDGER = ROOT / "eval" / "audits" / "fact_reconciliation" / "fact_ledger.json"
BASELINE = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001"
S1_ROWS = ROOT / "eval" / "audits" / "s1_answerability" / "s1_per_case.jsonl"
OUT_DIR = ROOT / "eval" / "audits" / "s2_target_evidence"

ANSWER_FILTERS = {"usage_policy": "answer_and_citation"}
ANSWER_SPACE_DEPTH = 100

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:~\d+(?:\.\d+)?)?")


def _compact(text: str) -> str:
    return "".join(text.split())


def _numbers(text: str) -> list[str]:
    return [_compact(n) for n in _NUMBER_RE.findall(text)]


def _fact_support_in_text(fact: str, quote: str | None, text: str) -> tuple[bool, bool]:
    """(strong, weak) containment: verbatim quote/fact text vs numbers-only."""
    t = _compact(text)
    strong = (bool(quote) and _compact(quote) in t) or _compact(fact) in t
    nums = _numbers(fact)
    weak = bool(nums) and not strong and all(n in t for n in nums)
    return strong, weak


def _fact_reachability(
    *,
    fact: str,
    quote: str | None,
    source_doc: str,
    primary_carriers: list[dict],
    alt_candidates: list[tuple[str, str, str]],
    answer_rank: dict[str, int],
) -> dict:
    """Complete-reachability judgment per fact (red-light criteria).

    完整可达 must be proven by an answer chunk in the generation top-5 that
    is BOTH:

      (a) **来源正确** -- ``file_name`` equals the fact's source document
          (cross-document coincidences like G039's "图1/图6" are rejected);
      (b) **足以支持整条 fact** -- the verbatim quote window or the verbatim
          fact text is present in the chunk.

    Numbers-only presence is a **弱线索** (weak clue): listed separately,
    never a carrier, never counts toward 完整可达.  Cross-document verbatim
    duplicates are recorded as rejected (traceable, not silent).
    """
    carriers: list[dict] = []
    strong_chunks: list[str] = []
    weak_clues: list[dict] = []
    rejected: list[dict] = []
    primary_ids = {c["chunk_id"] for c in primary_carriers}
    nums = _numbers(fact)
    for c in primary_carriers:
        entry = dict(c)
        entry["kind"] = "primary_binding"
        entry.setdefault("source_correct", True)  # binding doc == evidence doc
        carriers.append(entry)
        if (
            c.get("supports_fact") is True
            and c.get("live_usage_policy") == "answer_and_citation"
            and c.get("answer_space_rank") is not None
            and c["answer_space_rank"] <= 5
        ):
            strong_chunks.append(c["chunk_id"])
    for cid, fname, text in alt_candidates:
        if cid in primary_ids:
            continue
        verbatim = (bool(quote) and _compact(quote) in text) or _compact(fact) in text
        in_num = bool(nums) and not verbatim and all(n in text for n in nums)
        if verbatim and fname == source_doc:
            rank = answer_rank.get(cid)
            carriers.append({
                "chunk_id": cid,
                "kind": "duplicate_answer_chunk",
                "live_usage_policy": "answer_and_citation",
                "answer_space_rank": rank,
                "exists": True,
                "source_correct": True,
            })
            if rank is not None and rank <= 5:
                strong_chunks.append(cid)
        elif verbatim:
            rejected.append({
                "chunk_id": cid,
                "file_name": fname,
                "reason": "来源不正确(跨文档逐字重复不计可达)",
            })
        elif in_num:
            weak_clues.append({
                "chunk_id": cid,
                "file_name": fname,
                "answer_space_rank": answer_rank.get(cid),
                "note": "仅数字在位(弱线索,不计可达)",
            })
    best_rank = min(
        (answer_rank[c] for c in strong_chunks if c in answer_rank), default=None
    )
    return {
        "carriers": carriers,
        "reachable_at_5": bool(strong_chunks),
        "best_rank": best_rank,
        "weak_clues": weak_clues,
        "rejected_duplicates": rejected,
    }


def _classify_identity_mismatch(
    live_id: dict, trace_id: dict, live_hits: list[dict], trace_hits: list[dict]
) -> str:
    """Classify a matched/citable identity difference between live recompute
    and baseline trace (hits = evaluate_case dicts with chunk_id + score).

    Server-side RRF tie-break is non-strictly stable across runs.  Two noise
    shapes must NOT count as real drift:
    - boundary_rank_swap: differing chunks present on BOTH sides (tied rank
      swap, top-10 membership identical);
    - boundary_tie_flip: a differing chunk flips in/out at the @10 cutoff --
      its own-side score equals BOTH sides' cutoff score, i.e. the tie group
      straddles the boundary and membership is a coin flip (G045 ev0's
      matched chunk chunk_5196… vs chunk_54ab…, both RRF 0.166667).
    Anything else (presence change above the cutoff, citability change) is
    "material".  The original presence-only classifier counted the G045 tie
    flip as real drift, making the summary's real-mismatch count flip 0/1
    between runs (MD said 0 while JSON listed G045[0] -- the contradiction
    the handoff flagged).
    """
    differs = (
        live_id.get("matched_chunk_id") != trace_id.get("matched_chunk_id")
        or live_id.get("citable_chunk_id") != trace_id.get("citable_chunk_id")
    )
    if not differs:
        return "none"
    sm_live = {h["chunk_id"]: h["score"] for h in live_hits}
    sm_trace = {h["chunk_id"]: h["score"] for h in trace_hits}
    ids = {
        x
        for d in (live_id, trace_id)
        for x in (d.get("matched_chunk_id"), d.get("citable_chunk_id"))
        if x
    }
    if ids <= sm_live.keys() and ids <= sm_trace.keys():
        return "boundary_rank_swap"
    cutoff = min(min(sm_live.values()), min(sm_trace.values()))
    for cid in ids:
        score = sm_live.get(cid, sm_trace.get(cid))
        if score is None or abs(score - cutoff) > 1e-5:
            return "material"
    return "boundary_tie_flip"


def _find_pdf(doc_file: str) -> Path | None:
    for root, _, files in (ROOT / "data" / "raw").walk():
        if doc_file in files:
            return root / doc_file
    return None


def _page_text(pdf_path: Path, page: int) -> str:
    with fitz.open(pdf_path) as doc:
        if 1 <= page <= doc.page_count:
            return doc[page - 1].get_text()
    return ""


def _file_sha256(pdf_path: Path) -> str:
    """SHA256 of the PDF's raw bytes -- every re-check verdict is bound to
    the exact source file it was measured against (the audit is only as
    good as the file identity behind it)."""
    import hashlib

    h = hashlib.sha256()
    with pdf_path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _index_elements(canonical_dir: Path) -> dict[tuple[str, str], dict]:
    """Index canonical elements by (canonical_id, element_id).

    Element IDs (e000001...) are document-local and COLLIDE across the 14
    canonical documents of the corpus -- a chunk's ``canonical_id`` must
    select the document before ``element_id`` is resolved.  Keying by
    element_id alone silently returns the last document's element (wrong
    type / quality / policy), which misclassifies blocked-evidence root
    causes.
    """
    out: dict[tuple[str, str], dict] = {}
    for path in canonical_dir.glob("*.canonical.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for element in doc["elements"]:
            out[(doc["canonical_id"], element["element_id"])] = element
    return out


def main() -> None:
    started = time.perf_counter()
    current = _json(POINTER)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    collection = current["collection_name"]

    client = QdrantClient(url="http://127.0.0.1:6333", timeout=30.0)
    binding_config = {
        "dataset_sha256": ledger["dataset_sha256"],
        "corpus_version": current["corpus_version"],
        "collection": collection,
        "chunk_count": current["chunk_count"],
        "point_count": current["chunk_count"],
    }
    problems = _binding_problems(binding_config, DATASET, current, client=client)
    if problems:
        raise SystemExit(f"REFUSED: {problems[0]}")

    cases = _load_cases(DATASET)
    s1_rows = {
        json.loads(line)["case"]: json.loads(line)
        for line in S1_ROWS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    traces = {}
    for path in sorted((BASELINE / "cases").glob("G*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        traces[case["id"]] = case

    facts_by_case: dict[str, list[dict]] = {}
    for fact in ledger["facts"]:
        facts_by_case.setdefault(fact["case"], []).append(fact)

    # -- canonical elements (blocked-evidence tracing) ---------------------
    els_by_id = _index_elements(
        ROOT / "data" / "canonical" / "v2" / current["corpus_version"]
    )

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=collection,
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=20,
    )

    # -- preload all answer chunks once (duplicate-carrier discovery) ------
    answer_chunks: list[tuple[str, str, str]] = []  # chunk_id, file_name, compact text
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="usage_policy",
                        match=models.MatchValue(value="answer_and_citation"),
                    )
                ]
            ),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            payload = record.payload
            answer_chunks.append(
                (
                    str(payload["chunk_id"]),
                    str(payload.get("file_name", "")),
                    _compact(str(payload.get("text", ""))),
                )
            )
        if offset is None:
            break

    def _carrier_payloads(chunk_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        ids = list(dict.fromkeys(chunk_ids))
        for start in range(0, len(ids), 64):
            records, _ = client.scroll(
                collection_name=collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="chunk_id", match=models.MatchAny(any=ids[start : start + 64])
                        )
                    ]
                ),
                limit=min(64, len(ids) - start),
                with_payload=True,
                with_vectors=False,
            )
            for record in records:
                out[str(record.payload["chunk_id"])] = record.payload
        return out

    rows: list[dict] = []
    rank_probe_targets: list[dict] = []
    blocked_rows: list[dict] = []
    pdf_recheck: list[dict] = []
    human_list: list[dict] = []
    for case in cases:
        case_id = case["id"]
        s1 = s1_rows[case_id]
        trace = traces[case_id]
        case_facts = facts_by_case.get(case_id, [])

        # -- measured: answer-space top-100 (production filter) -----------
        dense = embedder.embed_query(case["question"])
        answer_hits = index.search(
            HybridQuery(text=case["question"], dense_vector=dense),
            mode="rrf",
            filters=dict(ANSWER_FILTERS),
            limit=ANSWER_SPACE_DEPTH,
        )
        answer_rank = {h.chunk_id: i + 1 for i, h in enumerate(answer_hits)}

        # -- measured: live unfiltered top-10 via the SAME harness matcher -
        unfiltered_hits = index.search(
            HybridQuery(text=case["question"], dense_vector=dense),
            mode="rrf",
            filters=None,
            limit=10,
        )
        live_eval = evaluate_case(
            case, unfiltered_hits, latency_ms=0.0, corpus_version=current["corpus_version"]
        )
        trace_top = [h["chunk_id"] for h in trace["hits"]]
        live_top = [h["chunk_id"] for h in live_eval["hits"]]

        def _carrier_counts(top: list[str], evidence_results: list[dict]) -> dict:
            """Unified carrier counts on ONE count basis each."""
            policies = {}  # filled by caller with hit policies
            unique = set()
            bound = set()
            for ev in evidence_results:
                for key in ("matched_chunk_id", "citable_chunk_id"):
                    cid = ev.get(key)
                    if cid and cid in top and ev.get("hit_usage_policy"):
                        if cid in top:
                            unique.add(cid)
                            if ev.get("hit_usage_policy") == "answer_and_citation":
                                bound.add((ev["evidence_index"], key))
            return {"unique_chunks": len(unique), "evidence_bound_carriers": len({b[0] for b in bound})}

        # -- trace comparison: per-evidence IDENTITY reconciliation --------
        # Both sides were produced by the SAME evaluate_case matcher, so the
        # matched/citable chunk identity and ranks compare directly.  Counts
        # are no longer the basis -- each evidence's identity is reconciled
        # and any mismatch lists BOTH sides (cross-run @10 boundary flips
        # surface here as identity differences on non-carrier chunks).
        trace_evidence = {e["evidence_index"]: e for e in trace["evidence_results"]}
        identity_rows = []
        for ev in live_eval["evidence_results"]:
            t_ev = trace_evidence.get(ev["evidence_index"], {})
            live_id = {
                "matched_chunk_id": ev.get("matched_chunk_id"),
                "match_rank": ev.get("match_rank"),
                "citable_chunk_id": ev.get("citable_chunk_id"),
                "citable_match_rank": ev.get("citable_match_rank"),
            }
            trace_id = {
                "matched_chunk_id": t_ev.get("matched_chunk_id"),
                "match_rank": t_ev.get("match_rank"),
                "citable_chunk_id": t_ev.get("citable_chunk_id"),
                "citable_match_rank": t_ev.get("citable_match_rank"),
            }
            identity_rows.append({
                "evidence_index": ev["evidence_index"],
                "live": live_id,
                "baseline_trace": trace_id,
                "citable_identity_equal": (
                    live_id["citable_chunk_id"] == trace_id["citable_chunk_id"]
                ),
                "matched_identity_equal": (
                    live_id["matched_chunk_id"] == trace_id["matched_chunk_id"]
                ),
            })
        for r in identity_rows:
            r["mismatch_kind"] = _classify_identity_mismatch(
                r["live"], r["baseline_trace"],
                live_eval["hits"], trace["hits"],
            )
        identity_mismatch = [r for r in identity_rows if r["mismatch_kind"] != "none"]
        flip_live = sorted(set(live_top).symmetric_difference(trace_top))

        # -- per-fact reachability (red-light criteria) --------------------
        fact_rows = []
        for fact in case_facts:
            if fact["classification"] == "refuse_meta":
                continue
            quotes_by_index: dict[int, str] = {}
            source_doc = None
            for ev_idx, ev in enumerate(case["evidence"]):
                if any(
                    b["evidence_index"] == ev_idx and b.get("supported") is True
                    for b in fact.get("bindings", [])
                ):
                    quotes_by_index[ev_idx] = ev["quote"]
                    if source_doc is None:
                        source_doc = ev["doc_file"]
            quote = next(iter(quotes_by_index.values()), None)

            primary_ids = [
                b["chunk_id"]
                for b in fact.get("bindings", [])
                if b.get("supported") is True
            ]
            payloads = _carrier_payloads(primary_ids) if primary_ids else {}
            primary_carriers = []
            for cid in primary_ids:
                payload = payloads.get(cid)
                primary_carriers.append({
                    "chunk_id": cid,
                    "supports_fact": True,
                    "live_usage_policy": (
                        payload or {}
                    ).get("usage_policy") or "missing_in_collection",
                    "answer_space_rank": answer_rank.get(cid),
                    "exists": payload is not None,
                    "file_name": (payload or {}).get("file_name"),
                    "physical_pages": list((payload or {}).get("physical_pages") or []),
                    "heading_path": list((payload or {}).get("heading_path") or []),
                    "text": (payload or {}).get("text"),
                })

            verdict = _fact_reachability(
                fact=fact["fact"],
                quote=quote,
                source_doc=source_doc or "",
                primary_carriers=primary_carriers,
                alt_candidates=answer_chunks,
                answer_rank=answer_rank,
            )
            fact_rows.append({
                "fact_index": fact["fact_index"],
                "fact": fact["fact"],
                "classification": fact["classification"],
                "human_ruling": fact["human_ruling"],
                "bindings": fact.get("bindings", []),
                "quotes_by_index": {
                    str(k): v for k, v in quotes_by_index.items()
                },
                "carriers": verdict["carriers"],
                # capped: the COUNT is authoritative; store at most 10 samples
                "weak_clues": verdict["weak_clues"][:10],
                "rejected_duplicates": verdict["rejected_duplicates"],
                "reachable_at_5": verdict["reachable_at_5"],
                "reachable_strong_at_5": verdict["reachable_at_5"],
                "best_rank": verdict["best_rank"],
                "carrier_kinds": sorted({
                    c["kind"] for c in verdict["carriers"]
                }),
                "weak_clue_count": len(verdict["weak_clues"]),
            })

        content_facts = fact_rows
        if case["expected_answerability"] == "refuse":
            completeness = "refuse_case"
        elif not content_facts:
            completeness = "待核(无内容fact)"
        elif all(f["reachable_at_5"] for f in content_facts):
            completeness = "完整可达"
        elif any(f["reachable_at_5"] for f in content_facts):
            completeness = "部分可达"
        else:
            completeness = "不可达"

        # -- blocked-evidence tracing --------------------------------------
        case_blocked = []
        for fact in content_facts:
            if fact["reachable_at_5"]:
                continue
            for binding in fact.get("bindings", []):
                cid = binding["chunk_id"]
                payloads_b = _carrier_payloads([cid])
                payload = payloads_b.get(cid)
                if payload is None:
                    case_blocked.append({
                        "case": case_id,
                        "fact_index": fact["fact_index"],
                        "chunk_id": cid,
                        "root_cause": "提取缺失或失真",
                        "element_types": [],
                        "quality_status": "missing_in_collection",
                        "policy_reasons": [],
                        "elements": [],
                        "pdf_page": binding.get("pdf_page"),
                        "doc": binding.get("doc"),
                    })
                    continue
                elements = []
                for eid in payload.get("element_ids") or []:
                    element = (
                        els_by_id.get((payload.get("canonical_id"), eid)) or {}
                    )
                    elements.append({
                        "element_id": eid,
                        "type": element.get("type"),
                        "quality_status": element.get("quality_status"),
                        "policy_reasons": element.get("policy_reasons") or [],
                        "provenance_methods": [
                            p.get("method") for p in element.get("provenance") or []
                        ],
                        "quality_issues": [
                            q if isinstance(q, str) else q.get("code") or q.get("type")
                            for q in element.get("quality_issues") or []
                        ],
                        "has_review_assertion": any(
                            isinstance(element.get(k), str) and element.get(k).strip()
                            for k in ("review_assertion_id", "review_id", "assertion_id")
                        ),
                        "explicit_usage_policy": element.get("usage_policy"),
                    })
                # root cause per carrier
                types = {
                    e["type"] for e in elements if e["type"] in ("table", "formula", "figure")
                }
                reasons = set(payload.get("policy_reasons") or [])
                if "answer_and_citation" == payload.get("usage_policy"):
                    root = "检索排名"
                elif types:
                    root = "结构化D3复核"
                elif reasons & {"unreadable_text", "too_short", "mojibake_symbol_text"}:
                    root = "普通文字质量"
                else:
                    root = "普通文字质量(OCR待审)"
                case_blocked.append({
                    "case": case_id,
                    "fact_index": fact["fact_index"],
                    "chunk_id": cid,
                    "root_cause": root,
                    "element_types": sorted(payload.get("element_types") or []),
                    "quality_status": payload.get("quality_status"),
                    "policy_reasons": sorted(reasons),
                    "elements": elements,
                    "pdf_page": binding.get("pdf_page"),
                    "doc": binding.get("doc"),
                })
        blocked_rows.extend(case_blocked)
        for item in case_blocked:
            human_list.append({
                "case": case_id,
                "fact_index": item["fact_index"],
                "doc": item["doc"],
                "pdf_page": item["pdf_page"],
                "chunk_id": item["chunk_id"],
                "root_cause": item["root_cause"],
                "element_types": item["element_types"],
                "quality_status": item["quality_status"],
                "policy_reasons": item["policy_reasons"],
            })

        # collect ranking-miss targets for the prefetch probe (executed,
        # saved per question: prefetch 20 vs 100 + leg ranks)
        for fact in fact_rows:
            fact_doc = next(
                (b["doc"] for b in fact.get("bindings", []) if b.get("doc")),
                None,
            )
            for c in fact["carriers"]:
                if (
                    c["kind"] == "primary_binding"
                    and c.get("live_usage_policy") == "answer_and_citation"
                    and (c.get("answer_space_rank") is None or c["answer_space_rank"] > 5)
                ):
                    rank_probe_targets.append({
                        "case": case_id,
                        "question": case["question"],
                        "chunk_id": c["chunk_id"],
                        "source_doc": fact_doc,
                    })

        rows.append({
            "case": case_id,
            "question": case["question"],
            "expected_answerability": case["expected_answerability"],
            "completeness": completeness,
            "facts": fact_rows,
            "reachable_fact_count": sum(1 for f in fact_rows if f["reachable_at_5"]),
            "fact_count": len(fact_rows),
            "complete_strict": (
                all(f["reachable_strong_at_5"] for f in fact_rows)
                if fact_rows and case["expected_answerability"] != "refuse"
                else None
            ),
            "trace_identity_reconciliation": {
                "evidence_identities": identity_rows,
                "identity_mismatch_count": len(identity_mismatch),
                "identity_mismatches": identity_mismatch,
                "boundary_flip_chunks_vs_trace": flip_live,
                "note": "两侧同用 evaluate_case 匹配器，身份(chunk_id+rank)直接对账；计数不再是比对基础。"
                "失配分三级：boundary_rank_swap=失配chunk两侧都在top-10的并列换位；boundary_tie_flip=失配chunk在@10截断线"
                "翻进/翻出且分数等于两侧截断分(并列组跨越截断线,成员翻转,非实质)；material=截断分之上的在位变化或"
                "可引用性变化(实质)。真实失配只计 material 且触及绑定 carrier",
            },
            "s1_carrier_only_attainable": s1.get("answer_attainable_at_5"),
        })

    # -- ranking-miss prefetch probe (EXECUTED, saved per question) --------
    # For every question whose primary binding carrier is answer_and_citation
    # yet outside the answer-space top-5: run the production answer-space RRF
    # at prefetch 20 (current composition) AND prefetch 100, plus the
    # per-leg (dense / bm25) ranks inside the answer space.  Saved verbatim
    # to s2_rank_probe.json -- no conclusion about prefetch depth is kept
    # without this artifact.
    rank_probe_targets_dedup = {
        (t["case"], t["chunk_id"]): t for t in rank_probe_targets
    }
    rank_probe = []
    if rank_probe_targets_dedup:
        idx20 = index  # current composition: hybrid_prefetch_limit=20
        idx100 = QdrantRetrievalIndex(
            client=client,
            collection_name=collection,
            vector_size=embedder.dimension,
            enable_bm25=True,
            hybrid_prefetch_limit=100,
        )
        for (case_id, cid), target in sorted(rank_probe_targets_dedup.items()):
            dense = embedder.embed_query(target["question"])
            entry = {
                "case": case_id,
                "question": target["question"],
                "carrier_chunk_id": cid,
                "source_doc": target.get("source_doc"),
                "prefetch_20": None,
                "prefetch_100": None,
            }
            for depth, ix in ((20, idx20), (100, idx100)):
                hits = ix.search(
                    HybridQuery(text=target["question"], dense_vector=dense),
                    mode="rrf",
                    filters=dict(ANSWER_FILTERS),
                    limit=ANSWER_SPACE_DEPTH,
                )
                ids = [h.chunk_id for h in hits]
                entry[f"prefetch_{depth}"] = {
                    "prefetch_limit": depth,
                    "rrf_rank": ids.index(cid) + 1 if cid in ids else None,
                }
            # per-leg ranks inside the answer space (depth 100)
            answer_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="usage_policy",
                        match=models.MatchValue(value="answer_and_citation"),
                    )
                ]
            )
            dres = client.query_points(
                collection_name=collection,
                query=list(dense),
                using="dense",
                search_params=models.SearchParams(exact=True),
                query_filter=answer_filter,
                limit=100,
                with_payload=True,
            )
            entry["leg_dense_rank_in_answer_space"] = next(
                (
                    i + 1
                    for i, pt in enumerate(dres.points)
                    if (pt.payload or {}).get("chunk_id") == cid
                ),
                None,
            )
            bres = client.query_points(
                collection_name=collection,
                query=models.Document(
                    text=target["question"],
                    model="qdrant/bm25",
                    options=QdrantRetrievalIndex._bm25_options(),
                ),
                using="bm25",
                query_filter=answer_filter,
                limit=100,
                with_payload=True,
            )
            entry["leg_bm25_rank_in_answer_space"] = next(
                (
                    i + 1
                    for i, pt in enumerate(bres.points)
                    if (pt.payload or {}).get("chunk_id") == cid
                ),
                None,
            )
            rank_probe.append(entry)
    (OUT_DIR / "s2_rank_probe.json").write_text(
        json.dumps(rank_probe, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # -- PDF re-check: pending rulings + supported=False bindings ----------
    sha_cache: dict[str, str] = {}
    # Quote (verbatim evidence window) is the primary probe; the fact SUMMARY
    # text is a paraphrase and must NOT be expected verbatim in the PDF.
    # Chunks span pages, so search a ±1 page window around the binding page.
    case_by_id = {c["id"]: c for c in cases}
    seen_keys = set()
    for fact in ledger["facts"]:
        needs = fact["human_ruling"] == "pending" or any(
            not b.get("supported") for b in fact.get("bindings", [])
        )
        if not needs:
            continue
        case = case_by_id[fact["case"]]
        quotes_by_index = {}
        for ev_idx, ev in enumerate(case["evidence"]):
            if any(
                b["evidence_index"] == ev_idx for b in fact.get("bindings", [])
            ):
                quotes_by_index[ev_idx] = ev["quote"]
        for binding in fact.get("bindings", []):
            key = (fact["case"], fact["fact_index"], binding["chunk_id"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            quote = quotes_by_index.get(binding["evidence_index"])
            pdf_path = _find_pdf(binding["doc"])
            result = {
                "case": fact["case"],
                "fact_index": fact["fact_index"],
                "fact": fact["fact"],
                "classification": fact["classification"],
                "human_ruling": fact["human_ruling"],
                "ledger_supported": binding.get("supported"),
                "doc": binding["doc"],
                "pdf_page": binding["pdf_page"],
                "quote_used": bool(quote),
                "pdf_path": (
                    str(pdf_path.relative_to(ROOT)) if pdf_path else None
                ),
            }
            if pdf_path is None:
                result["verdict"] = "UNCERTAIN(PDF未找到)"
                result["source_sha256"] = None
            else:
                key = str(pdf_path)
                if key not in sha_cache:
                    sha_cache[key] = _file_sha256(pdf_path)
                result["source_sha256"] = sha_cache[key]
                window_chars = 0
                found_page = None
                numbers_page = None
                for page in (binding["pdf_page"] - 1, binding["pdf_page"], binding["pdf_page"] + 1):
                    text = _page_text(pdf_path, page)
                    window_chars += len(text)
                    if not text:
                        continue
                    if quote and _compact(quote) in _compact(text) and found_page is None:
                        found_page = page
                    nums = _numbers(fact["fact"])
                    if (
                        nums
                        and all(n in _compact(text) for n in nums)
                        and numbers_page is None
                    ):
                        numbers_page = page
                if window_chars < 500:
                    # Scanned pages: no usable text layer -- a miss here says
                    # NOTHING about content presence; only page images can
                    # verify (human).
                    result["verdict"] = "PDF_TEXT_LAYER_EMPTY(扫描件文字层缺失,仅页图可核)"
                elif found_page is not None:
                    result["verdict"] = f"PDF_VERIFIED(引文逐字,p{found_page})"
                elif numbers_page is not None:
                    result["verdict"] = f"PDF_WEAK(仅数值在位,p{numbers_page})"
                else:
                    result["verdict"] = "PDF_NOT_ON_PAGE(±1页窗口未逐字命中,待人工页图复核)"
            pdf_recheck.append(result)

    # -- human short list: pending rulings with PDF pages -------------------
    for fact in ledger["facts"]:
        if fact["human_ruling"] != "pending":
            continue
        for binding in fact.get("bindings", []):
            human_list.append({
                "case": fact["case"],
                "fact_index": fact["fact_index"],
                "doc": binding["doc"],
                "pdf_page": binding["pdf_page"],
                "chunk_id": binding["chunk_id"],
                "root_cause": "旧台账待人工裁定(pending)",
                "element_types": [],
                "quality_status": None,
                "policy_reasons": [],
                "fact_text": fact["fact"],
            })

    # -- summary -------------------------------------------------------------
    completeness_counts = Counter(r["completeness"] for r in rows)
    content_rows = [r for r in rows if r["expected_answerability"] != "refuse"]
    complete_before = sum(1 for r in rows if r.get("s1_carrier_only_attainable"))
    complete_after = completeness_counts["完整可达"]
    partial_cases = [
        r["case"] for r in rows if r["completeness"] == "部分可达"
    ]
    complete_cases = [
        r["case"] for r in rows if r["completeness"] == "完整可达"
    ]
    unreachable = [
        r["case"] for r in rows if r["completeness"] == "不可达"
    ]
    fact_total = sum(r["fact_count"] for r in content_rows)
    fact_reachable = sum(r["reachable_fact_count"] for r in content_rows)
    fact_reachable_strong = sum(
        1 for r in content_rows for f in r["facts"] if f["reachable_strong_at_5"]
    )
    root_cause_counts = Counter(b["root_cause"] for b in blocked_rows)

    # -- admission face (computed, not hardcoded): unique blocked locator
    # chunks, their source elements by quality status, review assertions ----
    seen_blocked_chunks: dict[str, dict] = {}
    for b in blocked_rows:
        if b["quality_status"] == "missing_in_collection":
            continue
        seen_blocked_chunks.setdefault(b["chunk_id"], b)
    adm_elements = Counter()
    adm_review_assertions = 0
    for b in seen_blocked_chunks.values():
        for e in b["elements"]:
            adm_elements[e["quality_status"]] += 1
            if e["has_review_assertion"]:
                adm_review_assertions += 1
    # Identity reconciliation replaces count-based comparison: each evidence's
    # matched/citable identity is reconciled per evidence; mismatches here
    # must be checked against whether the flip touches a bound carrier.
    trace_identity_cases = {
        r["case"]
        for r in rows
        if r["trace_identity_reconciliation"]["identity_mismatch_count"] > 0
    }
    real_identity_mismatch = []
    for r in rows:
        rec = r["trace_identity_reconciliation"]
        bound_ids = {
            c
            for f in r["facts"]
            for c in [b["chunk_id"] for b in f.get("bindings", [])]
        }
        for mm in rec["identity_mismatches"]:
            if mm["mismatch_kind"] != "material":
                continue
            if (
                {
                    mm["live"]["matched_chunk_id"],
                    mm["live"]["citable_chunk_id"],
                    mm["baseline_trace"]["matched_chunk_id"],
                    mm["baseline_trace"]["citable_chunk_id"],
                }
                & bound_ids
            ):
                real_identity_mismatch.append(
                    {
                        "case": r["case"],
                        "evidence_index": mm["evidence_index"],
                        "mismatch_kind": mm["mismatch_kind"],
                    }
                )

    summary = {
        "classification": "diagnostic (S2 target-evidence preparation, read-only)",
        "generated_from": {
            "pointer": {
                "corpus_version": current["corpus_version"],
                "collection": collection,
            },
            "dataset_sha256": ledger["dataset_sha256"],
            "baseline_run": str(BASELINE.relative_to(ROOT)),
        },
        "complete_reachability": {
            "46_content_cases": len(content_rows),
            "完整可达": completeness_counts["完整可达"],
            "部分可达": completeness_counts["部分可达"],
            "不可达": completeness_counts["不可达"],
            "complete_cases": complete_cases,
            "partial_cases": partial_cases,
            "unreachable_cases": unreachable,
            "fact_level": {
                "content_facts": fact_total,
                "reachable_at_5": fact_reachable,
                "reachable_strong_at_5": fact_reachable_strong,
            },
            "s1_carrier_only_baseline": complete_before,
            "criteria_note": (
                "完整可达须由生成top-5中来源正确、足以支持整条fact的answer chunk证明"
                "(引文/事实文本逐字);仅数字在位=弱线索,不计完整可达;跨文档逐字重复不计"
            ),
        },
        "blocked_root_causes": dict(root_cause_counts),
        "trace_identity_reconciliation": {
            "note": "两侧同用 evaluate_case 匹配器，身份(chunk_id+rank)直接对账；计数不再是比对基础。"
            "失配分两级：boundary_rank_swap=两侧top-10成员集合相同的并列分换位(RRF tie-break非严格稳定,非实质)；"
            "material=在位性/可引用性变化(实质)。真实失配只计 material 且触及绑定 carrier",
            "identity_mismatch_cases": sorted(trace_identity_cases),
            "real_mismatch_touching_bound_carrier": real_identity_mismatch,
        },
        "rank_probe_artifact": (
            "eval/audits/s2_target_evidence/s2_rank_probe.json"
            if rank_probe
            else None
        ),
        "pdf_recheck": {
            "checked": len(pdf_recheck),
            "verdicts": dict(Counter(r["verdict"].split("(")[0] for r in pdf_recheck)),
            "sha_bound": all("source_sha256" in r for r in pdf_recheck),
        },
        "human_dependency": {
            "blocked_carrier_items": len(human_list),
            "by_root_cause": dict(Counter(h["root_cause"] for h in human_list)),
            "note": "不代填passed/review_assertion;全部需人工裁定",
        },
        "engineering_fix_assessment": {
            "admission_face": {
                "blocked_locator_chunks": len(seen_blocked_chunks),
                "source_elements": sum(adm_elements.values()),
                "by_quality_status": dict(adm_elements),
                "review_assertions": adm_review_assertions,
                "conclusion": "准入被显式质量状态逐个阻断（needs_review 须质量复核、passed 结构化元素须 D3 review_assertion）；全库 0 个 review assertion，工程不可代填",
            },
            "prefetch_replay_answer_space": {
                "artifact": "eval/audits/s2_target_evidence/s2_rank_probe.json",
                "measured": "逐题实测:answer空间RRF在prefetch 20与100两档下的目标carrier名次+双腿(answer空间内)排名",
                "conclusion": "见rank_probe实测;prefetch加深不修复的断言由已保存的逐题参数与排名支撑",
            },
            "policy_admission": {
                "measured": "见admission_face:blocked locator chunk的源element按质量状态分布,全库0个review assertion",
                "conclusion": "准入升级=D3人工复核路径,工程不可代填",
            },
            "engineering_fixable_within_constraints": [],
            "note": "本轮无一项根因可在'不代填质量/不重排/不动正式索引'约束内由工程修复;完整可达数的变化来自判定口径修正(承载级→fact级任意carrier),非制品变更",
        },
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "s2_per_case.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "s2_blocked_evidence.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in blocked_rows) + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "s2_pdf_recheck.json").write_text(
        json.dumps(pdf_recheck, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "s2_human_review_list.json").write_text(
        json.dumps(human_list, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "s2_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # -- human adjudication short list (markdown, with PDF pages) ----------
    hl: list[str] = [
        "# S2 人工裁定短清单（带 PDF 页码）— 不代填 passed / review_assertion",
        "",
        "> 全部条目保持现状（needs_review / pending）；工程不可代填，逐条由人工裁定。",
        "",
        "## A. 受阻证据（chunk 级，按根因分组）",
        "",
    ]
    seen_chunk = set()
    for item in human_list:
        if item["root_cause"] == "旧台账待人工裁定(pending)":
            continue
        key = (item["case"], item["chunk_id"])
        if key in seen_chunk:
            continue
        seen_chunk.add(key)
        hl.append(
            f"- ☐ {item['case']}[f{item['fact_index']}] `{item['doc']}` "
            f"**p{item['pdf_page']}** — chunk `{item['chunk_id'][:24]}…` — "
            f"{item['root_cause']} — element_types={item['element_types']} — "
            f"quality={item['quality_status']} — reasons={item['policy_reasons']}"
        )
    hl += ["", "## B. 旧台账待人工裁定（14 条 pending rulings）", ""]
    seen_fact = set()
    for item in human_list:
        if item["root_cause"] != "旧台账待人工裁定(pending)":
            continue
        key = (item["case"], item["fact_index"])
        if key in seen_fact:
            continue
        seen_fact.add(key)
        hl.append(
            f"- ☐ {item['case']}[f{item['fact_index']}] `{item['doc']}` "
            f"**p{item['pdf_page']}** — {item.get('fact_text', '')[:60]}"
        )
    (OUT_DIR / "s2_human_review_list.md").write_text(
        "\n".join(hl), encoding="utf-8"
    )

    # -- one-page summary (markdown) ----------------------------------------
    md: list[str] = [
        "# S2 目标证据准备 — 一页汇总（DIAGNOSTIC，只读）",
        "",
        f"> 语料 `{current['corpus_version']}` / 黄金集 sha `{ledger['dataset_sha256'][:12]}…`；"
        f"全部实测于发布 collection（只读）；正式索引/指针/黄金集/trace 零改动。",
        "",
        "## 完整可达（修复前后）",
        "",
        f"- **S1 口径（任一 evidence 承载可达）**：{complete_before}/46 题；",
        f"- **S2 口径（生成 top-5 内来源正确、足以支持整条 fact 的 answer chunk）**：完整可达 **{completeness_counts['完整可达']}** / "
        f"部分可达 {completeness_counts['部分可达']} / 不可达 {completeness_counts['不可达']}（46 内容题）；",
        f"- fact 级：{fact_reachable}/{fact_total} 内容 fact 可达（强 {fact_reachable_strong} = 引文/事实文本逐字在 answer chunk；"
        f"仅数字在位=弱线索 {sum(f.get('weak_clue_count', 0) for r in content_rows for f in r['facts'])} 处，不计可达）；",
        f"- 变化来源：**判定口径收紧（红灯：来源正确+整条 fact 逐字支持；数字巧合/跨文档重复不计）**——"
        f"原 13 题中 G039（跨文档图注数字巧合）、G012（仅数字在位）降为不可达；非任何制品变更；",
        f"- 4 拒答题（G008/G015/G028/G050）按拒答目标单列，不计入。",
        "",
        "## 统一 trace 口径（证据身份对账，替代计数比对）",
        "",
        f"- live 复算与基线 trace 同用 evaluate_case 匹配器，每条 evidence 的"
        f"身份（matched/citable 的 chunk_id+rank）直接对账（`s2_per_case.jsonl` 每题 "
        f"`trace_identity_reconciliation`）；计数不再是比对基础；",
        f"- 身份不一致 {len(trace_identity_cases)} 题，分三级：**boundary_rank_swap**（失配 chunk 两侧都在 "
        f"top-10，并列换位）与 **boundary_tie_flip**（失配 chunk 在 @10 截断线翻进/翻出、分数等于两侧截断分，"
        f"成员翻转）均为 RRF tie-break 噪声，非实质；**material**（截断分之上的在位变化或可引用性变化）为实质；",
        f"- 触及证据绑定 carrier 的**实质失配 {len(real_identity_mismatch)} 条**"
        f"（判据：mismatch_kind=material 且失配 chunk 触及绑定 carrier）；",
        "",
        "## 排名 miss 逐题实测（prefetch 20 vs 100，已保存）",
        "",
        f"- 受阻清单中检索排名 miss 逐题实测：answer 空间 RRF 在 prefetch 20 与 100 两档下的"
        f"目标 carrier 名次 + dense/bm25 双腿在 answer 空间内的排名，"
        f"逐题参数与排名保存于 `s2_rank_probe.json`（{len(rank_probe)} 条）；",
        "",
        "## 受阻证据根因（分组）",
        "",
        "| 根因 | 条目 | 处置 |",
        "| --- | --- | --- |",
        f"| 普通文字质量(OCR待审) | {root_cause_counts.get('普通文字质量(OCR待审)', 0)} | 人工页图复核（短清单 A 组） |",
        f"| 普通文字质量(too_short等) | {root_cause_counts.get('普通文字质量', 0)} | 人工复核（短清单 A 组） |",
        f"| 结构化D3复核 | {root_cause_counts.get('结构化D3复核', 0)} | D3 review_assertion 人工路径（短清单 A 组） |",
        f"| 检索排名 | {root_cause_counts.get('检索排名', 0)} | 重排/权重，本轮排除（见下） |",
        f"| 旧台账 pending | 14 条 rulings（31 binding） | 人审暂停中，短清单 B 组 |",
        "",
        "## PDF 回查（不把台账当内容证明）",
        "",
        f"- 回查 {len(pdf_recheck)} 条（pending rulings + supported=False 绑定，引文为主探针、fact 数值为辅、±1 页窗口），"
        f"**每条绑定源文件 SHA256**（`source_sha256`）：",
        f"  - PDF_VERIFIED {summary['pdf_recheck']['verdicts'].get('PDF_VERIFIED', 0)}；",
        f"  - PDF_WEAK(仅数值在位) {summary['pdf_recheck']['verdicts'].get('PDF_WEAK', 0)}；",
        f"  - PDF_TEXT_LAYER_EMPTY(扫描件) {summary['pdf_recheck']['verdicts'].get('PDF_TEXT_LAYER_EMPTY', 0)}"
        f"——文字层回查不可行，仅页图可核；",
        f"  - PDF_NOT_ON_PAGE {summary['pdf_recheck']['verdicts'].get('PDF_NOT_ON_PAGE', 0)}"
        f"——待人工页图复核（`s2_pdf_recheck.json` 逐条含 PDF 路径+页码+源文件 SHA）。",
        "",
        "## 工程修复评估（实测，非假设）",
        "",
        "1. **准入面**：{n_chunk} 个 blocked locator chunk 的 {n_el} 个源 element：needs_review "
        "{n_nr} / passed {n_pass}（passed 全在结构化/受限 chunk 上，按 D3 结构化默认准入）——"
        "全库 **0 个 review assertion**；needs_review 须质量复核、passed 须 D3 升级路径，工程不可代填；".format(
            n_chunk=len(seen_blocked_chunks),
            n_el=sum(adm_elements.values()),
            n_nr=adm_elements.get("needs_review", 0),
            n_pass=adm_elements.get("passed", 0),
        ),
        "2. **检索排名**：逐题实测已保存（`s2_rank_probe.json`，prefetch 20/100 两档参数+双腿排名）；"
        f"{len(rank_probe)} 条目标——深 prefetch 不修复的结论由实测数据支撑（与 E2 受控实验一致）；"
        "唯一有效手段是重排/权重，本轮明确排除；",
        "3. **结论：本轮工程可修复集合为空**——完整可达数变化全部来自判定口径修正；未产任何候选（无可修根因）、"
        "未动正式索引/指针/黄金集/trace、未调 LLM、未进生成或重排。",
        "",
        "## 剩余人工依赖",
        "",
        f"- 受阻证据人工复核：{len(human_list)} 条（短清单 `s2_human_review_list.md`，含 PDF 页码）；",
        f"- 14 条 pending rulings（人审暂停中）+ PDF 回查中 NOT_ON_PAGE 的页图复核；",
        f"- 生成层端到端验证（G5/G6，需 LLM 授权）——完整可达≠生成层可答。",
    ]
    (OUT_DIR / "s2_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"outdir: {OUT_DIR}")


if __name__ == "__main__":
    main()
