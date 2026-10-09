"""Regression tests for run report rendering.

A diagnostic run must never render as a plain "Retrieval Baseline": the HTML
title and banner carry the classification (非正式基线), the pending/unfrozen
counts, and the latency trio must match summary.json exactly.  A formal run
renders as a baseline without the diagnostic banner.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.evaluation.retrieval_harness import _render_html, regenerate_report


def _result(case_id: str = "G001") -> dict:
    return {
        "id": case_id,
        "question": "测试问题？",
        "labels": {"repr": ["数值"], "challenge": ["表格"], "modality": ["表格"]},
        "metrics": {
            "retrieval_applicable": True,
            "required_evidence_count": 1,
            "matched_evidence_at_5": 1,
            "matched_evidence_at_10": 1,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 1.0,
            "latency_ms": 12.5,
        },
        "evidence_results": [],
        "hits": [],
    }


def _summary() -> dict:
    return {
        "macro": {
            "recall_at_5": 0.75,
            "recall_at_10": 0.85,
            "mrr": 0.6,
        },
        "by_policy": {
            "citable": {"recall_at_5": 0.5, "recall_at_10": 0.9},
            "locator_only": {"recall_at_5": 0.8, "recall_at_10": 0.85},
        },
        "latency_ms": {"p50": 23.86, "p95": 33.301, "mean": 25.35444},
    }


def _config(classification: str) -> dict:
    return {
        "run_id": "diagnostic-test-run" if classification == "diagnostic" else "formal-test-run",
        "corpus_version": "corpus-test",
        "classification": classification,
        "pending_markers": 7,
        "unfrozen_case_count": 3,
    }


def test_diagnostic_report_is_marked_not_a_formal_baseline() -> None:
    rendered = _render_html(_config("diagnostic"), [_result()], _summary())
    assert "非正式基线" in rendered
    assert "DIAGNOSTIC RUN" in rendered
    assert "not a formal baseline" in rendered
    # the pending / unfrozen counts from config appear in the banner
    assert "7" in rendered and "3" in rendered
    # it must not render under an unqualified baseline title
    assert "<title>Retrieval Baseline</title>" not in rendered


def test_formal_report_renders_as_baseline_without_banner() -> None:
    rendered = _render_html(_config("formal"), [_result()], _summary())
    assert "Retrieval Baseline" in rendered
    assert "非正式基线" not in rendered
    assert "DIAGNOSTIC RUN" not in rendered


def test_report_latency_trio_matches_summary_exactly() -> None:
    rendered = _render_html(_config("diagnostic"), [_result()], _summary())
    summary = _summary()
    for key in ("p50", "p95", "mean"):
        expected = f"{summary['latency_ms'][key]:.3f}"
        assert expected in rendered, f"latency {key}={expected} missing from report"


def test_regenerate_report_rebuilds_html_from_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "cases").mkdir(parents=True)
    (run_dir / "config.json").write_text(
        json.dumps(_config("diagnostic"), ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "summary.json").write_text(
        json.dumps(_summary(), ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "cases" / "G001.json").write_text(
        json.dumps(_result(), ensure_ascii=False), encoding="utf-8"
    )
    report = regenerate_report(run_dir)
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "非正式基线" in text
    assert "cases/G001.json" in text
    # regeneration must not touch the recorded artifacts
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) == _summary()
