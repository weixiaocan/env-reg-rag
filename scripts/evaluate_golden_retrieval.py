"""Run the reviewed golden set against the published Qdrant RRF retrieval path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.retrieval_harness import (  # noqa: E402
    evaluate_case,
    summarize_results,
    write_run_artifacts,
)
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex  # noqa: E402


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cases(path: Path) -> list[dict]:
    raw = path.read_bytes().decode("utf-8")
    rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
    if len(rows) != 50 or [row.get("id") for row in rows] != [f"G{i:03d}" for i in range(1, 51)]:
        raise ValueError("golden dataset must contain G001-G050 in order")
    if any(row.get("status") not in {"reviewed", "frozen"} for row in rows):
        raise ValueError("retrieval evaluation requires reviewed or frozen cases")
    return rows


def _pending_items(cases: list[dict]) -> list[str]:
    """Human-readable list of unclosed manual-pending markers in the dataset."""
    items: list[str] = []
    for case in cases:
        for index, evidence in enumerate(case.get("evidence") or []):
            for pending in evidence.get("pending") or []:
                items.append(f"{case['id']}[{index}]:{pending}")
    return items


def _formal_blockers(cases: list[dict]) -> list[str]:
    """Everything that still blocks a formal (reportable) run.

    A formal run requires BOTH zero unclosed manual-pending markers AND every
    case at status ``frozen`` -- closing the pending checklist alone leaves the
    dataset at ``reviewed``, which per eval/golden_v2.summary.md is still a
    diagnostic-only state (human PDF sign-off not yet recorded).
    """
    blockers = _pending_items(cases)
    for case in cases:
        if case.get("status") != "frozen":
            blockers.append(
                f"{case['id']}:status={case.get('status') or 'missing'} (must be frozen)"
            )
    return blockers


FACT_LEDGER = ROOT / "eval" / "audits" / "fact_reconciliation" / "fact_ledger.json"


def _fact_ledger_blockers(dataset_path: Path, ledger_path: Path | None = None,
                          full: bool = False,
                          corpus: Any | None = None,
                          rulings_path: Path | None = None) -> list[str]:
    """Round-4 rework: a formal run must validate the fact ledger bound to
    the current dataset hash.  Missing, unreadable, stale-hash, or any fact
    with ``human_ruling=pending`` all refuse -- frozen statuses alone never
    clear the gate.

    ``full=True`` (used by --formal) additionally runs the authoritative
    acceptance over ALL fact coverage and bindings -- a same-hash ledger
    with missing, duplicate or misbound entries is refused.  Cheap checks
    only for diagnostic runs.
    """
    ledger_file = Path(ledger_path) if ledger_path else FACT_LEDGER
    if full:
        from scripts.reconcile_fact_evidence import authoritative_acceptance

        problems = list(
            authoritative_acceptance(dataset_path, ledger_file, rulings_path, corpus=corpus)
        )
        # pending human rulings are a valid ledger state but still block
        # formal -- append them when the ledger is readable and hash-bound
        if ledger_file.is_file():
            try:
                ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
                sha = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
                if ledger.get("dataset_sha256") == sha:
                    pending = [
                        f"{entry.get('case')}[f{entry.get('fact_index')}]:human_ruling=pending"
                        for entry in ledger.get("facts") or []
                        if entry.get("human_ruling") == "pending"
                    ]
                    if pending:
                        problems.append(
                            f"fact ledger holds {len(pending)} fact(s) with "
                            f"human_ruling=pending (e.g. {pending[0]}) -- human "
                            "adjudication required before formal"
                        )
                        problems.extend(pending)
            except (OSError, json.JSONDecodeError):
                pass
        return problems
    if not ledger_file.is_file():
        return [
            "fact-ledger missing "
            f"({ledger_file.name}) -- run scripts/reconcile_fact_evidence.py --write"
        ]
    try:
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"fact-ledger unreadable ({ledger_file.name}): {exc}"]
    sha = hashlib.sha256(Path(dataset_path).read_bytes()).hexdigest()
    if ledger.get("dataset_sha256") != sha:
        return [
            "fact-ledger hash stale: ledger "
            f"{str(ledger.get('dataset_sha256'))[:16]}… != dataset {sha[:16]}… "
            "-- regenerate the ledger before a formal run"
        ]
    pending = [
        f"{entry.get('case')}[f{entry.get('fact_index')}]:human_ruling=pending"
        for entry in ledger.get("facts") or []
        if entry.get("human_ruling") == "pending"
    ]
    if pending:
        return [
            f"fact ledger holds {len(pending)} fact(s) with human_ruling=pending "
            f"(e.g. {pending[0]}) -- human adjudication required before formal"
        ] + pending
    return []


def _default_run_id() -> str:
    return datetime.now(UTC).strftime("retrieval-%Y%m%dT%H%M%SZ")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run(args: argparse.Namespace) -> Path:
    dataset_path = args.dataset.resolve()
    cases = _load_cases(dataset_path)
    current = _json(ROOT / "data" / "registry" / "corpus-current.json")
    corpus_version = current["corpus_version"]

    # Diagnostic/formal gate: a formal run needs BOTH zero unclosed
    # manual-pending markers AND every case frozen.  Anything else can only
    # ever produce a diagnostic run; formal metrics are hard-refused so the
    # two can never be conflated.
    pending = _pending_items(cases)
    blockers = _formal_blockers(cases) + _fact_ledger_blockers(dataset_path, full=args.formal)
    if args.formal and blockers:
        raise SystemExit(
            "REFUSED: formal retrieval metrics require a dataset with zero "
            f"pending markers, every case frozen, and a fact ledger bound to "
            f"the current dataset hash with zero pending human rulings, but "
            f"{len(blockers)} blockers remain (e.g. {blockers[0]}). Close the "
            "manual review checklist, freeze every case and clear the fact "
            "ledger, or run without --formal to produce a diagnostic run."
        )
    classification = "formal" if (args.formal and not blockers) else "diagnostic"

    collection = args.collection or os.getenv("QDRANT_COLLECTION") or current["collection_name"]
    qdrant_url = args.qdrant_url or os.getenv("QDRANT_URL", "http://127.0.0.1:6333")

    client = QdrantClient(url=qdrant_url, timeout=args.timeout)
    if not client.collection_exists(collection):
        raise RuntimeError(f"Qdrant collection is unavailable: {collection} at {qdrant_url}")
    collection_info = client.get_collection(collection)
    point_count = int(collection_info.points_count or 0)
    expected_count = int(current.get("chunk_count") or 0)
    if expected_count and point_count != expected_count:
        raise RuntimeError(
            f"published point count mismatch: pointer={expected_count}, qdrant={point_count}"
        )

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=collection,
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=args.prefetch_limit,
    )

    results = []
    started_at = datetime.now(UTC)
    for number, case in enumerate(cases, start=1):
        started = time.perf_counter()
        dense = embedder.embed_query(case["question"])
        hits = index.search(
            HybridQuery(text=case["question"], dense_vector=dense),
            mode="rrf",
            filters=None,
            limit=args.limit,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        result = evaluate_case(
            case, hits, latency_ms=latency_ms, corpus_version=corpus_version
        )
        results.append(result)
        print(
            f"[{number:02d}/50] {case['id']} "
            f"R@5={result['metrics']['recall_at_5']} "
            f"R@10={result['metrics']['recall_at_10']} "
            f"{latency_ms:.1f}ms"
        )

    finished_at = datetime.now(UTC)
    run_id = args.run_id or _default_run_id()
    if classification == "diagnostic" and not run_id.startswith("diagnostic-"):
        run_id = f"diagnostic-{run_id}"
    run_dir = args.output_root.resolve() / run_id
    config = {
        "schema_version": "retrieval-eval-run/1.0",
        "run_id": run_id,
        "classification": classification,
        "pending_markers": len(pending),
        "unfrozen_case_count": sum(1 for row in cases if row.get("status") != "frozen"),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "dataset": _display_path(dataset_path),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "corpus_version": corpus_version,
        "collection": collection,
        "qdrant_url": qdrant_url,
        "point_count": point_count,
        "retrieval_mode": "qdrant-dense-bm25-rrf",
        "embedding_model": embedder.model_id,
        "embedding_revision": embedder.model_revision,
        "limit": args.limit,
        "prefetch_limit": args.prefetch_limit,
        "primary_k": 5,
        "secondary_k": 10,
        "case_count": len(cases),
        "matching": "doc_page_then_strict_quote_ids_upgrade",
        "metrics_note": (
            "Recall is split by usage_policy on BOTH sides: citable_* counts "
            "hits on answer_and_citation evidence whose carrying chunk also "
            "projects to answer_and_citation (citable_downgraded_* counts "
            "answer-annotated evidence matched only by a locator-only chunk); "
            "locator_only_* counts hits on source_locator_only evidence and "
            "is location support only, never citable answer support."
        ),
    }
    summary = summarize_results(results)
    summary["run_id"] = run_id
    summary["classification"] = classification
    summary["corpus_version"] = corpus_version
    summary["collection"] = collection
    if classification == "diagnostic":
        summary["diagnostic_notice"] = (
            "DIAGNOSTIC RUN - not a formal baseline. The dataset still carries "
            f"{len(pending)} unclosed manual-pending markers and "
            f"{sum(1 for row in cases if row.get('status') != 'frozen')} cases "
            "below status=frozen. Numbers below must not be quoted as formal "
            "Recall."
        )
    write_run_artifacts(run_dir=run_dir, config=config, results=results, summary=summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {run_dir / 'report.html'}")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "eval" / "golden_v8.jsonl",
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "eval" / "runs")
    parser.add_argument("--run-id")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--collection")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--prefetch-limit", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--formal",
        action="store_true",
        help=(
            "Request a formal (reportable) run. Hard-refused while the dataset "
            "carries any unclosed manual-pending marker or any case below "
            "status=frozen; without this flag every run is classified diagnostic."
        ),
    )
    args = parser.parse_args()
    if args.limit < 10:
        parser.error("--limit must be at least 10 to report @10")
    if args.prefetch_limit < args.limit:
        parser.error("--prefetch-limit must be greater than or equal to --limit")
    run(args)


if __name__ == "__main__":
    main()
