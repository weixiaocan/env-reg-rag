"""Deterministic retrieval evaluation for the reviewed golden dataset.

This module is deliberately independent of Qdrant and the embedding model.  It
evaluates already-returned hits and writes stable run artifacts; the CLI owns
the production retrieval composition.
"""

from __future__ import annotations

import html
import json
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _get(hit: Any, name: str, default: Any) -> Any:
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)


def _quote_matches(quote: str, text: str) -> bool:
    """Strict quote-to-chunk text corroboration.

    Containment is the only strong signal: either the quote appears in the
    chunk text (forward, at least 8 normalized chars), or the chunk text is a
    *substantial* part of the quote (reverse containment, at least 30 chars
    and 40% of the quote -- a short fragment inside a long golden quote is
    not evidence of the same content).  A partial common fragment counts only
    when it is long in absolute terms (>= 18 chars) and a near-majority of
    the shorter side (>= 0.40); the previous 8-char / 0.28 floor accepted
    unrelated same-page content sharing a short boilerplate run.
    """
    quote_normalized = _normalized(quote)
    text_normalized = _normalized(text)
    if not quote_normalized or not text_normalized:
        return False
    if quote_normalized in text_normalized:
        return len(quote_normalized) >= 8
    if text_normalized in quote_normalized:
        return len(text_normalized) >= max(30, int(0.4 * len(quote_normalized)))

    # Long OCR/page excerpts are often split differently at chunk time.  A
    # shared fragment must be both meaningful and a material share of the
    # shorter side before it corroborates the evidence.
    from difflib import SequenceMatcher

    longest = SequenceMatcher(
        None, quote_normalized, text_normalized, autojunk=False
    ).find_longest_match().size
    shorter = max(1, min(len(quote_normalized), len(text_normalized)))
    return longest >= 18 and longest / shorter >= 0.40


def _match_method(evidence: dict[str, Any], hit: Any) -> str | None:
    """Decide how (or whether) a hit corroborates one evidence row.

    T1 hardening rules -- a hit NEVER counts on ID agreement alone:

    * every hit must be in the evidence document AND on the evidence page;
    * every hit must pass the strict quote corroboration
      (:func:`_quote_matches`) -- this is the content gate that rejects
      same-page, same-ID hits whose text is unrelated content (ID aliasing
      inside one corpus build is otherwise undetectable from the hit);
    * an element-ID intersection only *upgrades* the method label of an
      already quote-corroborated hit (``quote_and_ids``); it is never
      sufficient by itself.

    Cross-corpus aliasing is additionally refused upstream: the dataset
    records ``anchor_corpus`` and ``evaluate_case`` hard-fails when the run
    does not target exactly that corpus version.
    """
    if evidence.get("doc_file") != _get(hit, "file_name", ""):
        return None
    page = evidence.get("page")
    pages = set(_get(hit, "physical_pages", []) or [])
    if page is None or page not in pages:
        return None
    if not _quote_matches(evidence.get("quote") or "", _get(hit, "text", "")):
        return None
    expected_ids = set(evidence.get("element_ids") or [])
    actual_ids = set(_get(hit, "element_ids", []) or [])
    if expected_ids and actual_ids and expected_ids.intersection(actual_ids):
        return "quote_and_ids"
    return "page_quote"


def _first_rank(predicate: Any, hits: Sequence[Any]) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if predicate(hit):
            return rank
    return None


def _at(rank: int | None, limit: int) -> bool:
    return rank is not None and rank <= limit


def evaluate_case(
    case: dict[str, Any],
    hits: Sequence[Any],
    *,
    latency_ms: float,
    corpus_version: str | None = None,
) -> dict[str, Any]:
    """Evaluate one golden case against at most the first ten retrieval hits.

    When the case carries ``anchor_corpus`` (golden v2+) the run MUST supply
    a matching ``corpus_version``: a missing version is treated exactly like
    a mismatching one (no silent scoring of possibly-stale element IDs), and
    a mismatch raises instead of scoring IDs from a different corpus build.
    """
    anchor_corpus = case.get("anchor_corpus")
    if anchor_corpus:
        if not corpus_version:
            raise ValueError(
                f"{case.get('id', '?')} is anchored to {anchor_corpus} but no "
                "corpus_version was supplied: refusing to score element IDs "
                "against an unverified corpus build"
            )
        if anchor_corpus != corpus_version:
            raise ValueError(
                f"{case.get('id', '?')} is anchored to {anchor_corpus} but the run "
                f"targets {corpus_version}: element IDs are not comparable across "
                "corpus builds"
            )
    top_hits = list(hits[:10])
    required = [item for item in case.get("evidence") or [] if item.get("must_hit", True)]
    evidence_results: list[dict[str, Any]] = []

    for index, evidence in enumerate(required):
        document_rank = _first_rank(
            lambda hit, name=evidence["doc_file"]: _get(hit, "file_name", "") == name,
            top_hits,
        )
        page_rank = _first_rank(
            lambda hit, name=evidence["doc_file"], page=evidence["page"]: (
                _get(hit, "file_name", "") == name
                and page in (_get(hit, "physical_pages", []) or [])
            ),
            top_hits,
        )
        match_rank = None
        match_method = None
        matched_chunk_id = None
        matched_hit_policy = None
        citable_rank = None
        citable_chunk_id = None
        citable_method = None
        locator_rank = None
        locator_chunk_id = None
        locator_method = None
        # Scan the WHOLE top-10: the first hit must never shadow later hits.
        # A locator-only chunk that corroborates the quote early must not hide
        # an answer_and_citation chunk that corroborates it later -- citable
        # @5/@10 are ranked on the first *citable* hit, not the first hit.
        for rank, hit in enumerate(top_hits, start=1):
            method = _match_method(evidence, hit)
            if method is None:
                continue
            hit_policy = _get(hit, "usage_policy", "") or None
            if match_rank is None:
                match_rank = rank
                match_method = method
                matched_chunk_id = _get(hit, "chunk_id", "")
                matched_hit_policy = hit_policy
            if hit_policy == "answer_and_citation":
                if citable_rank is None:
                    citable_rank = rank
                    citable_chunk_id = _get(hit, "chunk_id", "")
                    citable_method = method
            elif locator_rank is None:
                locator_rank = rank
                locator_chunk_id = _get(hit, "chunk_id", "")
                locator_method = method
            if citable_rank is not None and locator_rank is not None:
                break
        evidence_results.append(
            {
                "evidence_index": index,
                "doc_file": evidence["doc_file"],
                "page": evidence["page"],
                "element_ids": evidence.get("element_ids") or [],
                "quote": evidence.get("quote") or "",
                "usage_policy": evidence.get("usage_policy") or "",
                "document_rank": document_rank,
                "page_rank": page_rank,
                "match_rank": match_rank,
                "match_method": match_method,
                "matched_chunk_id": matched_chunk_id,
                "hit_usage_policy": matched_hit_policy,
                "citable_match_rank": citable_rank,
                "citable_chunk_id": citable_chunk_id,
                "citable_match_method": citable_method,
                "locator_match_rank": locator_rank,
                "locator_chunk_id": locator_chunk_id,
                "locator_match_method": locator_method,
            }
        )

    required_count = len(required)
    applicable = required_count > 0

    def fraction(field: str, limit: int) -> float | None:
        if not applicable:
            return None
        return sum(_at(item[field], limit) for item in evidence_results) / required_count

    relevant_ranks = [
        item["match_rank"] for item in evidence_results if item["match_rank"] is not None
    ]
    recall_at_5 = fraction("match_rank", 5)
    recall_at_10 = fraction("match_rank", 10)

    # Policy split, gated on BOTH sides and ranked independently: a hit only
    # counts as citable answer support when the golden evidence is
    # answer_and_citation AND a chunk that projects to answer_and_citation
    # corroborates it.  citable @K uses the first *citable* corroborating hit
    # -- an earlier locator-only corroboration shadows nothing.  A downgrade
    # at K means: the evidence matched within K, but NO citable corroboration
    # exists within that same K (a citable hit at rank 7 clears @10 even when
    # a locator chunk matched at rank 2; @5 in that scenario is a downgrade).
    citable = [
        item for item in evidence_results
        if item["usage_policy"] == "answer_and_citation"
    ]
    locator_only = [
        item for item in evidence_results
        if item["usage_policy"] == "source_locator_only"
    ]

    def _matched(item: dict[str, Any], limit: int) -> bool:
        return _at(item["match_rank"], limit)

    def _citable_hit(item: dict[str, Any], limit: int) -> bool:
        return _at(item["citable_match_rank"], limit)

    def _downgraded(item: dict[str, Any], limit: int) -> bool:
        return _matched(item, limit) and not _citable_hit(item, limit)

    def _policy_metrics(
        items: list[dict[str, Any]],
        prefix: str,
        *,
        hit_predicate=None,
    ) -> dict[str, Any]:
        count = len(items)
        matched = hit_predicate or _matched
        m5 = sum(matched(item, 5) for item in items)
        m10 = sum(matched(item, 10) for item in items)
        metrics = {
            f"{prefix}_evidence_count": count,
            f"{prefix}_matched_at_5": m5,
            f"{prefix}_matched_at_10": m10,
            f"{prefix}_recall_at_5": round(m5 / count, 6) if count else None,
            f"{prefix}_recall_at_10": round(m10 / count, 6) if count else None,
        }
        if hit_predicate is _citable_hit:
            metrics[f"{prefix}_downgraded_at_5"] = sum(_downgraded(item, 5) for item in items)
            metrics[f"{prefix}_downgraded_at_10"] = sum(_downgraded(item, 10) for item in items)
        return metrics

    metrics = {
        "retrieval_applicable": applicable,
        "required_evidence_count": required_count,
        "matched_evidence_at_5": sum(_at(item["match_rank"], 5) for item in evidence_results),
        "matched_evidence_at_10": sum(_at(item["match_rank"], 10) for item in evidence_results),
        "recall_at_5": recall_at_5,
        "recall_at_10": recall_at_10,
        "document_hit_at_5": fraction("document_rank", 5),
        "document_hit_at_10": fraction("document_rank", 10),
        "page_hit_at_5": fraction("page_rank", 5),
        "page_hit_at_10": fraction("page_rank", 10),
        "complete_recall_at_5": bool(applicable and recall_at_5 == 1.0),
        "complete_recall_at_10": bool(applicable and recall_at_10 == 1.0),
        "mrr": (1.0 / min(relevant_ranks)) if relevant_ranks else (0.0 if applicable else None),
        "latency_ms": round(float(latency_ms), 3),
        **_policy_metrics(citable, "citable", hit_predicate=_citable_hit),
        **_policy_metrics(locator_only, "locator_only"),
    }

    traces = []
    for rank, hit in enumerate(top_hits, start=1):
        matched = [
            item["evidence_index"]
            for item in evidence_results
            if item["match_rank"] == rank
        ]
        traces.append(
            {
                "rank": rank,
                "chunk_id": _get(hit, "chunk_id", ""),
                "score": round(float(_get(hit, "score", 0.0)), 6),
                "file_name": _get(hit, "file_name", ""),
                "physical_pages": list(_get(hit, "physical_pages", []) or []),
                "element_ids": list(_get(hit, "element_ids", []) or []),
                "usage_policy": _get(hit, "usage_policy", ""),
                "heading_path": list(_get(hit, "heading_path", []) or []),
                "text": _get(hit, "text", ""),
                "matched_evidence_indexes": matched,
            }
        )

    return {
        "id": case["id"],
        "question": case["question"],
        "labels": case.get("labels") or {},
        "expected_facts": case.get("expected_facts") or [],
        "metrics": metrics,
        "evidence_results": evidence_results,
        "hits": traces,
    }


def _mean(values: Iterable[float | None]) -> float | None:
    materialized = [float(value) for value in values if value is not None]
    return round(statistics.fmean(materialized), 6) if materialized else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _metric_block(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in results if item["metrics"]["retrieval_applicable"]]
    required = sum(item["metrics"]["required_evidence_count"] for item in eligible)
    matched_5 = sum(item["metrics"]["matched_evidence_at_5"] for item in eligible)
    matched_10 = sum(item["metrics"]["matched_evidence_at_10"] for item in eligible)
    return {
        "case_count": len(results),
        "eligible_case_count": len(eligible),
        "macro": {
            name: _mean(item["metrics"][name] for item in eligible)
            for name in (
                "recall_at_5", "recall_at_10", "mrr",
                "document_hit_at_5", "document_hit_at_10",
                "page_hit_at_5", "page_hit_at_10",
                "citable_recall_at_5", "citable_recall_at_10",
                "locator_only_recall_at_5", "locator_only_recall_at_10",
            )
        },
        "micro": {
            "required_evidence_count": required,
            "matched_evidence_at_5": matched_5,
            "matched_evidence_at_10": matched_10,
            "recall_at_5": round(matched_5 / required, 6) if required else None,
            "recall_at_10": round(matched_10 / required, 6) if required else None,
        },
        "by_policy": {
            "citable": _policy_micro(eligible, "citable"),
            "locator_only": _policy_micro(eligible, "locator_only"),
        },
        "complete_case_rate_at_5": _mean(
            float(item["metrics"]["complete_recall_at_5"]) for item in eligible
        ),
        "complete_case_rate_at_10": _mean(
            float(item["metrics"]["complete_recall_at_10"]) for item in eligible
        ),
    }


def _policy_micro(eligible: Sequence[dict[str, Any]], prefix: str) -> dict[str, Any]:
    """Micro aggregation of the citable / locator-only policy split."""
    count = sum(item["metrics"][f"{prefix}_evidence_count"] for item in eligible)
    m5 = sum(item["metrics"][f"{prefix}_matched_at_5"] for item in eligible)
    m10 = sum(item["metrics"][f"{prefix}_matched_at_10"] for item in eligible)
    block = {
        "evidence_count": count,
        "matched_at_5": m5,
        "matched_at_10": m10,
        "recall_at_5": round(m5 / count, 6) if count else None,
        "recall_at_10": round(m10 / count, 6) if count else None,
    }
    if prefix == "citable":
        # Visibility for annotation/hit policy mismatches: answer-annotated
        # evidence whose carrying chunk is locator-only.
        block["downgraded_at_5"] = sum(
            item["metrics"].get("citable_downgraded_at_5", 0) for item in eligible
        )
        block["downgraded_at_10"] = sum(
            item["metrics"].get("citable_downgraded_at_10", 0) for item in eligible
        )
    return block


def summarize_results(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    block = _metric_block(results)
    latencies = [float(item["metrics"]["latency_ms"]) for item in results]
    by_label: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        for dimension in ("repr", "challenge", "modality"):
            for label in (result.get("labels") or {}).get(dimension) or []:
                grouped[f"{dimension}:{label}"].append(result)
    for label, members in sorted(grouped.items()):
        by_label[label] = _metric_block(members)
    return {
        **block,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "mean": _mean(latencies),
        },
        "by_label": by_label,
    }


def _format_metric(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _render_html(config: dict[str, Any], results: Sequence[dict[str, Any]], summary: dict[str, Any]) -> str:
    macro = summary["macro"]
    # A diagnostic run must never render as a plain "Retrieval Baseline":
    # the title and the banner both carry the classification so the HTML
    # cannot be quoted as a formal baseline by accident.
    classification = str(config.get("classification") or "diagnostic")
    if classification == "formal":
        title = "Retrieval Baseline（formal）"
        banner = ""
    else:
        title = "检索评估 — DIAGNOSTIC（非正式基线）"
        banner = (
            "<p class=\"banner\">DIAGNOSTIC RUN — 非正式基线（not a formal baseline）："
            f"pending 待核 {config.get('pending_markers', 'N/A')} 项 · "
            f"未 frozen {config.get('unfrozen_case_count', 'N/A')} 题 · "
            "以下数字不得引用为正式 Recall。</p>"
        )
    by_policy = summary.get("by_policy") or {}
    citable = by_policy.get("citable") or {}
    locator = by_policy.get("locator_only") or {}
    latency = summary["latency_ms"]
    rows = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            "<tr>"
            f"<td><a href='cases/{html.escape(result['id'])}.json'>{html.escape(result['id'])}</a></td>"
            f"<td>{html.escape(result['question'])}</td>"
            f"<td>{_format_metric(metrics['recall_at_5'])}</td>"
            f"<td>{_format_metric(metrics['recall_at_10'])}</td>"
            f"<td>{_format_metric(metrics['mrr'])}</td>"
            f"<td>{_format_metric(metrics['latency_ms'])}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:system-ui;margin:32px;color:#172033}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{padding:14px 18px;background:#f3f6fa;border-radius:10px}}table{{border-collapse:collapse;width:100%;margin-top:22px}}th,td{{padding:8px;border-bottom:1px solid #dbe2ea;text-align:left}}th{{background:#eef3f8}}td:nth-child(n+3){{white-space:nowrap}}.banner{{padding:12px 16px;background:#fdecea;border:1px solid #f2b8b5;border-radius:8px;color:#8a1c11;font-weight:600}}</style>
</head><body><h1>{html.escape(title)}</h1>
<p>run: {html.escape(str(config.get('run_id', '')))} · corpus: {html.escape(str(config.get('corpus_version', '')))} · classification: {html.escape(classification)}</p>
{banner}
<div class="cards"><div class="card">Recall@5 (macro)<br><strong>{_format_metric(macro['recall_at_5'])}</strong></div>
<div class="card">Recall@10 (macro)<br><strong>{_format_metric(macro['recall_at_10'])}</strong></div>
<div class="card">MRR<br><strong>{_format_metric(macro['mrr'])}</strong></div>
<div class="card">citable R@5 / @10 (micro)<br><strong>{_format_metric(citable.get('recall_at_5'))} / {_format_metric(citable.get('recall_at_10'))}</strong></div>
<div class="card">locator-only R@5 / @10 (micro)<br><strong>{_format_metric(locator.get('recall_at_5'))} / {_format_metric(locator.get('recall_at_10'))}</strong></div>
<div class="card">latency p50 / p95 / mean (ms)<br><strong>{_format_metric(latency['p50'])} / {_format_metric(latency['p95'])} / {_format_metric(latency['mean'])}</strong></div></div>
<table><thead><tr><th>ID</th><th>Question</th><th>R@5</th><th>R@10</th><th>MRR</th><th>ms</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""


def regenerate_report(run_dir: Path) -> Path:
    """Rebuild report.html from a finished run's artifacts (config/summary/cases).

    Read-only with respect to the run's recorded data: only report.html is
    rewritten, so a report rendering fix can be applied to an existing run
    without re-executing the evaluation.
    """
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    results = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "cases").glob("*.json"))
    ]
    report = run_dir / "report.html"
    report.write_text(_render_html(config, results, summary), encoding="utf-8")
    return report


def write_run_artifacts(
    *,
    run_dir: Path,
    config: dict[str, Any],
    results: Sequence[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for result in results:
        (cases_dir / f"{result['id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "report.html").write_text(
        _render_html(config, results, summary), encoding="utf-8"
    )
