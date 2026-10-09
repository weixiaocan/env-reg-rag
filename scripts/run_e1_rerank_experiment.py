"""E1 experiment runner: local cross-encoder rerank over the RRF candidate set.

Single-variable experiment per the approved E1_CONFIG.json (G1 in
OPTIMIZATION_PLAN.md).  The ONLY variable vs the baseline composition is the
local rerank stage -- everything else is pinned by ``unchanged_fields``:

* RRF with the SAME per-leg prefetch 20 (dense 20 + bm25 20), server-side
  fusion candidate set <=40 -- the fusion request asks for the FULL candidate
  set (``limit = 2 * prefetch_limit = 40``);
* rerank reorders ONLY (``src/retrieval/rerank.py::RerankStage`` -- never
  filters, never mutates hits, policy/source fields untouched);
* the final top-10 is truncated AFTER the rerank (``limit 10`` unchanged);
* with rerank disabled (``--disable-rerank``) the stage is an identity over
  the full candidate list -- the final top-10 is then exactly the baseline
  RRF top-10 (integration-tested with a fake index/scorer).

The scorer is built FAIL-CLOSED: the snapshot is resolved from the local HF
cache first (``local_files_only=True`` -- a cache miss must hard-fail, the
runner never downloads a model) and its NON-EMPTY IMMUTABLE commit sha is
recorded before any weight loads; model and tokenizer are loaded from that
one snapshot path.  An unconfirmable revision refuses the run.  HF model
downloads require explicit project-owner authorization; running this runner
is not such an authorization.

Pre-run evidence gates (all hard refusals BEFORE any search):

* dataset sha256 == the approved E1 config, corpus binding via the probe
  validator, runtime embedding model ID/revision == baseline
  ``unchanged_fields``;
* control rounds (``--disable-rerank``) carry an INDEPENDENT run id
  (``diagnostic-exp-e1-control-...``) and every run refuses an existing
  run directory (ids are never reused or overwritten).

The rerank latency is summarized separately (``summary.rerank_latency_ms``
p50/p95/mean) and the unfrozen case count is recorded in the run config.

Classification is ALWAYS diagnostic (no --formal flag exists here); formal
metrics stay gated behind the frozen workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_golden_retrieval import _json, _load_cases, _pending_items  # noqa: E402
from scripts.probe_miss_rerank import _binding_problems  # noqa: E402
from src.evaluation.retrieval_harness import (  # noqa: E402
    _mean,
    _percentile,
    evaluate_case,
    summarize_results,
    write_run_artifacts,
)
from src.retrieval.bge_small_zh import BgeSmallZhEmbedder  # noqa: E402
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex  # noqa: E402
from src.retrieval.rerank import RerankConfig, RerankStage  # noqa: E402

E1_CONFIG = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001" / "E1_CONFIG.json"
REGISTRY_POINTER = ROOT / "data" / "registry" / "corpus-current.json"

# Precise HF download scope for BAAI/bge-reranker-base (filtered snapshot):
# EXACTLY the files transformers resolves per-file for
# AutoModelForSequenceClassification + AutoTokenizer -- nothing else.  The
# repo also carries pytorch_model.bin (pickle duplicate) and onnx/model.onnx
# (total snapshot 3,359,121,141 bytes ≈ 3.36 GB); those are NOT load
# requirements and are excluded.  Recorded file sizes/commit come from a
# read-only metadata query (files_metadata=True) -- no model download.
FILTERED_ALLOW_PATTERNS = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
)
EXCLUDED_FROM_SCOPE = ("pytorch_model.bin", "onnx/model.onnx", "README.md", ".gitattributes")


_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _resolve_snapshot(
    model_id: str,
    revision: str | None,
    *,
    patterns: tuple[str, ...] = FILTERED_ALLOW_PATTERNS,
    _snapshot_download: Any = None,
) -> tuple[Path, str]:
    """The single real path ``filtered download -> offline run``.

    Resolves the model+tokenizer snapshot from the LOCAL HF cache using the
    SAME ``FILTERED_ALLOW_PATTERNS`` the (authorized) download uses, so the
    offline resolution can never drift from the download scope.  The
    revision MUST be the pinned 40-hex repo sha: ``None`` or a branch ref
    is refused -- there is NO fallback to ``main``.  The resolved snapshot
    must sit under exactly the pinned revision directory and must contain
    EVERY required file; any gap hard-fails.

    Returns ``(snapshot_path, commit_sha)``; both the model and the
    tokenizer are loaded from that one snapshot path (same-snapshot
    guarantee).  Fail-closed: a model missing from the cache raises
    (never downloads).
    """
    if not revision or not _COMMIT_SHA_RE.fullmatch(revision):
        raise RuntimeError(
            f"refusing to resolve {model_id!r}: revision must be the pinned "
            f"40-hex commit sha (got {revision!r}) -- no fallback to main"
        )
    if _snapshot_download is None:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "huggingface_hub unavailable: cannot confirm the cached "
                "snapshot revision -- refusing (download NOT authorized)"
            ) from exc
    else:
        snapshot_download = _snapshot_download
    try:
        path = Path(
            snapshot_download(
                repo_id=model_id,
                revision=revision,
                local_files_only=True,
                allow_patterns=list(patterns),
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"filtered snapshot for {model_id!r} (revision={revision}, "
            f"allow_patterns={len(patterns)} file(s)) is not in the local HF "
            "cache; download NOT authorized -- refusing"
        ) from exc
    commit = path.name
    if commit != revision:
        raise RuntimeError(
            f"snapshot revision mismatch for {model_id!r}: cache holds "
            f"{commit!r} != pinned {revision!r} -- refusing to run"
        )
    missing = [pattern for pattern in patterns if not (path / pattern).is_file()]
    if missing:
        raise RuntimeError(
            f"filtered snapshot for {model_id!r} is incomplete: missing "
            f"required file(s) {missing} -- refusing to run"
        )
    return path, commit


def _build_scorer(
    rerank_config: RerankConfig,
    *,
    pinned_revision: str,
    snapshot_resolver: Any = None,
) -> tuple[Any, str]:
    """Build the local cross-encoder scorer -- fail-closed, never downloads.

    ``pinned_revision`` is the E1 config's ``repo_sha`` (the immutable
    commit the authorized download will fetch); resolution happens ONLY at
    that revision -- no fallback to main.  Returns ``(score,
    confirmed_revision)`` where the confirmed revision is always the pinned
    sha, and both the model and the tokenizer are loaded from that one
    snapshot path (same-snapshot guarantee).
    """
    snapshot_path, confirmed_revision = _resolve_snapshot(
        rerank_config.model_id,
        pinned_revision,
        _snapshot_download=snapshot_resolver,
    )
    try:
        import torch  # noqa: F401
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "E1 rerank requires the local inference stack (transformers/torch); "
            "the runner never downloads a model -- install the stack locally "
            "and place the model in the HF cache first"
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(str(snapshot_path), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(snapshot_path), local_files_only=True
    ).to(rerank_config.device)
    model.eval()

    def score(question: str, text: str) -> float:
        inputs = tokenizer(
            question,
            text,
            truncation=True,
            max_length=rerank_config.max_length,
            return_tensors="pt",
        ).to(rerank_config.device)
        with torch.inference_mode():
            logits = model(**inputs).logits
        return float(logits[0][0])

    return score, confirmed_revision


def _embedding_problems(unchanged: dict, embedder: Any) -> list[str]:
    """Runtime embedding binding check: the embedder actually built at run
    time must be the same model ID AND revision pinned by the baseline
    ``unchanged_fields`` -- a drifted embedder would make the experiment
    incomparable to the baseline."""
    problems: list[str] = []
    expected_model = unchanged.get("embedding_model")
    expected_revision = unchanged.get("embedding_revision")
    if embedder.model_id != expected_model:
        problems.append(
            f"embedding model drifted: runtime {embedder.model_id!r} != "
            f"baseline unchanged_fields {expected_model!r}"
        )
    if embedder.model_revision != expected_revision:
        problems.append(
            f"embedding revision drifted: runtime {embedder.model_revision!r} != "
            f"baseline unchanged_fields {expected_revision!r}"
        )
    return problems


def _compose_run_id(date_str: str, *, disable_rerank: bool, round_number: int) -> str:
    """Control rounds carry an INDEPENDENT run id -- never shared with (or
    overwritten by) a rerank round of the same date."""
    prefix = "diagnostic-exp-e1-control" if disable_rerank else "diagnostic-exp-e1-rerank"
    return f"{prefix}-{date_str}-r{round_number}"


def _refuse_existing_run_dir(run_dir: Path) -> None:
    """Run ids are never reused or overwritten: an existing directory is a
    hard refusal BEFORE any search runs."""
    if run_dir.exists():
        raise SystemExit(
            f"REFUSED: run directory already exists: {_display_path(run_dir)} -- "
            "run ids are never reused; pick the next round or move the old "
            "run aside"
        )


def run_cases(
    cases: list[dict],
    *,
    index: Any,
    embedder: Any,
    stage: RerankStage,
    corpus_version: str,
    prefetch_limit: int,
    limit: int,
) -> list[dict]:
    """Evaluate every case through the E1 composition.

    Per case: dense embed -> RRF fusion request for the FULL candidate set
    (``limit = 2 * prefetch_limit``, each leg prefetch ``prefetch_limit``) ->
    rerank (reorder-only) -> truncate to the final top-10 AFTER the rerank ->
    deterministic harness evaluation.  ``rerank_latency_ms`` is recorded
    single-column per case (新增重排耗时单列).
    """
    results: list[dict] = []
    candidate_limit = 2 * prefetch_limit
    for case in cases:
        started = time.perf_counter()
        dense = embedder.embed_query(case["question"])
        candidates = index.search(
            HybridQuery(text=case["question"], dense_vector=dense),
            mode="rrf",
            filters=None,
            limit=candidate_limit,
        )
        rerank_started = time.perf_counter()
        ranked = stage.rerank(case["question"], candidates)
        rerank_latency_ms = (time.perf_counter() - rerank_started) * 1000.0
        hits = ranked[:limit]
        latency_ms = (time.perf_counter() - started) * 1000.0
        result = evaluate_case(
            case, hits, latency_ms=latency_ms, corpus_version=corpus_version
        )
        result["rerank_latency_ms"] = rerank_latency_ms
        results.append(result)
    return results


def _default_client_factory(url: str, timeout: float) -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(url=url, timeout=timeout)


def _default_embedder_factory() -> Any:
    return BgeSmallZhEmbedder(local_files_only=True, device="cpu")


def run(
    args: argparse.Namespace,
    *,
    client_factory: Any = None,
    embedder_factory: Any = None,
    scorer_builder: Any = None,
) -> Path:
    """Run the E1 experiment.  ``client_factory`` / ``embedder_factory`` /
    ``scorer_builder`` are injection points for the end-to-end gate tests --
    with the defaults, nothing is constructed or queried until every pre-run
    gate has passed; the run-id/directory gate in particular fires BEFORE
    any Qdrant query, any model load and any scorer build."""
    client_factory = client_factory or _default_client_factory
    embedder_factory = embedder_factory or _default_embedder_factory
    scorer_builder = scorer_builder or (
        lambda config: _build_scorer(config, pinned_revision=pinned_sha)
    )

    e1 = _json(Path(args.e1_config))
    if e1.get("experiment") != "E1-rerank":
        raise SystemExit(f"not an E1 config: {args.e1_config}")

    dataset_path = args.dataset.resolve()
    cases = _load_cases(dataset_path)

    unchanged = e1["unchanged_fields"]
    if args.limit != unchanged["limit"]:
        raise SystemExit(
            f"REFUSED: limit {args.limit} != unchanged_fields limit "
            f"{unchanged['limit']} -- the final top-K is a pinned baseline field"
        )
    if args.prefetch_limit != unchanged["prefetch_limit"]:
        raise SystemExit(
            f"REFUSED: prefetch-limit {args.prefetch_limit} != unchanged_fields "
            f"prefetch_limit {unchanged['prefetch_limit']} -- deepening prefetch "
            "is E2's variable, never mixed into E1"
        )

    dataset_sha = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    if dataset_sha != e1["dataset_sha256"]:
        raise SystemExit(
            f"REFUSED: dataset sha {dataset_sha[:16]}… != E1 config "
            f"{e1['dataset_sha256'][:16]}… -- E1 is bound to the approved "
            "golden dataset; regenerate the config first"
        )

    # The run revision is FIXED to the config's repo sha -- the exact commit
    # the authorized filtered download will fetch.  No fallback to main.
    pinned_sha = (e1.get("model_download") or {}).get("repo_sha")
    if not pinned_sha or not _COMMIT_SHA_RE.fullmatch(pinned_sha):
        raise SystemExit(
            "REFUSED: E1 config model_download.repo_sha must pin a 40-hex "
            "commit sha -- the run revision is fixed to the downloaded "
            "snapshot, no fallback to main"
        )

    # Run identity + directory gate FIRST -- before any Qdrant query, any
    # model load and any scorer build: a run id is never reused, and an
    # existing directory must refuse before side effects of any kind.
    run_id = _compose_run_id(
        datetime.now(UTC).strftime("%Y%m%d"),
        disable_rerank=args.disable_rerank,
        round_number=args.round,
    )
    run_dir = args.output_root.resolve() / run_id
    _refuse_existing_run_dir(run_dir)

    current = _json(REGISTRY_POINTER)
    binding_config = {
        "dataset_sha256": e1["dataset_sha256"],
        "corpus_version": e1["corpus_version"],
        "collection": e1["collection"],
        "point_count": e1["unchanged_fields"]["point_count"],
    }
    qdrant_url = args.qdrant_url or "http://127.0.0.1:6333"

    client = client_factory(url=qdrant_url, timeout=args.timeout)
    problems = _binding_problems(
        binding_config, dataset_path, current, client=client
    )
    if problems:
        raise SystemExit(
            f"REFUSED: {len(problems)} dataset/corpus binding problem(s), "
            f"e.g. {problems[0]}"
        )

    corpus_version = current["corpus_version"]
    collection = e1["collection"]
    if not client.collection_exists(collection):
        raise RuntimeError(f"Qdrant collection is unavailable: {collection} at {qdrant_url}")

    embedder = embedder_factory()
    embedding_problems = _embedding_problems(e1["unchanged_fields"], embedder)
    if embedding_problems:
        raise SystemExit(
            f"REFUSED: {len(embedding_problems)} embedding binding problem(s), "
            f"e.g. {embedding_problems[0]} -- the runtime embedder must match "
            "the baseline unchanged_fields exactly"
        )
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=collection,
        vector_size=embedder.dimension,
        enable_bm25=True,
        hybrid_prefetch_limit=args.prefetch_limit,
    )

    rerank_config = RerankConfig(
        model_id=e1["variable"]["model_id"],
        revision=pinned_sha,
        device=e1["variable"].get("device", "cpu"),
        max_length=e1["variable"].get("max_length", 512),
        final_top_k=args.limit,
    )
    confirmed_revision: str | None
    if args.disable_rerank:
        stage = RerankStage(config=rerank_config, scorer=None)
        confirmed_revision = None  # control round: no model involved
    else:
        scorer, confirmed_revision = scorer_builder(rerank_config)
        if confirmed_revision != pinned_sha:
            raise SystemExit(
                f"REFUSED: resolved revision {confirmed_revision!r} != pinned "
                f"repo_sha {pinned_sha!r} -- the run revision is fixed, no "
                "fallback to main"
            )
        rerank_config = replace(rerank_config, revision=confirmed_revision)
        stage = RerankStage(config=rerank_config, scorer=scorer)

    started_at = datetime.now(UTC)
    results = run_cases(
        cases,
        index=index,
        embedder=embedder,
        stage=stage,
        corpus_version=corpus_version,
        prefetch_limit=args.prefetch_limit,
        limit=args.limit,
    )

    config = {
        "schema_version": "e1-experiment-run/1.0",
        "run_id": run_id,
        "classification": "diagnostic",
        "experiment": "E1-rerank",
        "e1_config": _display_path(Path(args.e1_config)),
        "baseline_run_id": e1["baseline_run_id"],
        "dataset": _display_path(dataset_path),
        "dataset_sha256": dataset_sha,
        "corpus_version": corpus_version,
        "collection": collection,
        "qdrant_url": qdrant_url,
        "round": args.round,
        "rerank_enabled": not args.disable_rerank,
        "variable": {
            "stage": "local-cross-encoder-rerank",
            "model_id": rerank_config.model_id,
            "revision": confirmed_revision,
            "revision_source": (
                "pinned to the E1 config repo_sha (no fallback to main); "
                "resolved from the local HF cache snapshot directory under "
                "the download's FILTERED_ALLOW_PATTERNS before any weight "
                "load; model and tokenizer share this one snapshot"
                if confirmed_revision
                else "control round -- no rerank model involved"
            ),
            "device": rerank_config.device,
            "max_length": rerank_config.max_length,
        },
        "depth_semantics": {
            "candidate_fetch_depth_per_leg": args.prefetch_limit,
            "fusion_request_limit": 2 * args.prefetch_limit,
            "final_top_k": args.limit,
            "note": "每腿 prefetch 20 -> 融合候选 ≤40 -> rerank 重排 -> 重排后截取 top-10",
        },
        "embedding_model": embedder.model_id,
        "embedding_revision": embedder.model_revision,
        "limit": args.limit,
        "prefetch_limit": args.prefetch_limit,
        "pending_markers": len(_pending_items(cases)),
        "unfrozen_case_count": sum(
            1 for row in cases if row.get("status") != "frozen"
        ),
        "diagnostic_notice": (
            "DIAGNOSTIC E1 EXPERIMENT - not a formal baseline. Single variable "
            "vs baseline: local cross-encoder rerank. Numbers must not be "
            "quoted as formal Recall."
        ),
    }
    summary = summarize_results(results)
    summary["run_id"] = run_id
    summary["classification"] = "diagnostic"
    summary["corpus_version"] = corpus_version
    summary["collection"] = collection
    summary["rerank_enabled"] = not args.disable_rerank
    rerank_latencies = [float(r["rerank_latency_ms"]) for r in results]
    summary["rerank_latency_ms"] = {
        "p50": _percentile(rerank_latencies, 0.50),
        "p95": _percentile(rerank_latencies, 0.95),
        "mean": _mean(rerank_latencies),
    }
    write_run_artifacts(run_dir=run_dir, config=config, results=results, summary=summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"report: {run_dir / 'report.html'}")
    return run_dir


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "eval" / "golden_v2.jsonl")
    parser.add_argument("--output-root", type=Path, default=ROOT / "eval" / "runs")
    parser.add_argument("--e1-config", type=Path, default=E1_CONFIG)
    parser.add_argument("--round", type=int, choices=(1, 2), required=True)
    parser.add_argument(
        "--disable-rerank",
        action="store_true",
        help="Identity stage over the full candidate list -- final top-10 must "
        "equal the baseline composition (equivalence check run).",
    )
    parser.add_argument("--qdrant-url")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--prefetch-limit", type=int, default=20)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
