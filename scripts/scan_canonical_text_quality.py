#!/usr/bin/env python
"""Scan page-intermediate / assembled corpus text readability (P3 quality gate).

Writes ``data/observability/garble_scan_<corpus_version>.json`` and a mixed/garbled
review queue for human follow-up (P3-c minimal).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.application.corpus_artifacts import resolve_current_corpus  # noqa: E402
from src.ingestion.text_quality import document_text_quality  # noqa: E402


def _load_page_docs(corpus_version: str) -> list[dict[str, Any]]:
    path = _REPO_ROOT / "data" / "canonical" / f"{corpus_version}-documents.jsonl"
    docs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def scan(corpus_version: str) -> dict[str, Any]:
    docs = _load_page_docs(corpus_version)
    reports = [document_text_quality(d) for d in docs]
    page_reason_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    bad_pages = 0
    total_pages = 0
    page_samples = []
    for report in reports:
        total_pages += report["pages"]
        bad_pages += report["bad_pages"]
        verdict_counts[report["verdict"]] += 1
        for reason, n in (report.get("bad_reasons") or {}).items():
            page_reason_counts[reason] += n
        if report["verdict"] in {"garbled_text", "mixed"} and report.get("sample"):
            page_samples.append(
                {
                    "display": report["display"],
                    "sha256": report["sha256"],
                    "verdict": report["verdict"],
                    "sample_page": report["sample_page"],
                    "sample_label": report["sample_label"],
                    "sample": report["sample"],
                }
            )
    out = {
        "corpus_version": corpus_version,
        "total_docs": len(reports),
        "total_pages": total_pages,
        "bad_pages": bad_pages,
        "bad_page_ratio": round(bad_pages / total_pages, 4) if total_pages else 0.0,
        "page_reason_counts": dict(page_reason_counts),
        "verdict_counts": dict(verdict_counts),
        "documents": reports,
        "page_samples": page_samples[:40],
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-version", default=None)
    args = parser.parse_args(argv)
    corpus_version = args.corpus_version or resolve_current_corpus(_REPO_ROOT).corpus_version
    report = scan(corpus_version)
    out_dir = _REPO_ROOT / "data" / "observability"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"garble_scan_{corpus_version}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    queue = [
        d for d in report["documents"]
        if d["verdict"] in {"garbled_text", "mixed"}
    ]
    queue_path = out_dir / f"text_quality_review_queue_{corpus_version}.json"
    queue_path.write_text(
        json.dumps(
            {
                "corpus_version": corpus_version,
                "count": len(queue),
                "items": queue,
                "note": "P3-c minimal queue: mixed/garbled docs for human review; not a full UI.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({
        "wrote": str(out_path.relative_to(_REPO_ROOT)).replace("\\", "/"),
        "review_queue": str(queue_path.relative_to(_REPO_ROOT)).replace("\\", "/"),
        "total_docs": report["total_docs"],
        "total_pages": report["total_pages"],
        "bad_pages": report["bad_pages"],
        "verdict_counts": report["verdict_counts"],
        "garbled": [d["display"] for d in report["documents"] if d["verdict"] == "garbled_text"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
