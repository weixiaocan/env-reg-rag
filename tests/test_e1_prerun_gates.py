"""Counterexample tests for the E1 runner's pre-run evidence gates.

Each gate must hard-refuse its tampered counterexample BEFORE any search
runs -- none of these tests touch Qdrant, a model, or the network:

* revision evidence: the snapshot revision must come from the local HF
  cache as a non-empty immutable 40-hex commit (model + tokenizer share
  that one snapshot); an unconfirmable revision (branch name, missing
  model, hub absent) refuses;
* embedding binding: the runtime embedder's model ID and revision must
  equal the baseline ``unchanged_fields`` -- a drift refuses;
* run identity: control rounds carry an independent run id and an
  existing run directory is refused (ids never reused/overwritten).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_e1_rerank_experiment import (  # noqa: E402
    _compose_run_id,
    _embedding_problems,
    _refuse_existing_run_dir,
    _resolve_snapshot,
    FILTERED_ALLOW_PATTERNS,
    run,
)

REPO_SHA = "2cfc18c9415c912f9d8155881c133215df768a70"


def _fake_hub(snapshot_root: Path, cached_files: list[str], snapshot_name: str = REPO_SHA):
    """Offline hub fake: the repo's FULL tree is known (manifest), but the
    local cache holds ONLY the allowed files' snapshot.  The fake asserts it
    is called exactly like the authorized download: pinned revision + the
    same FILTERED_ALLOW_PATTERNS + local_files_only."""
    snapshot = snapshot_root / "snapshots" / snapshot_name
    snapshot.mkdir(parents=True)
    for name in cached_files:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")

    def fake(**kwargs):
        assert kwargs["revision"] == REPO_SHA
        assert tuple(kwargs["allow_patterns"]) == FILTERED_ALLOW_PATTERNS
        assert kwargs["local_files_only"] is True
        return snapshot

    return fake

BASELINE_UNCHANGED = {
    "embedding_model": "BAAI/bge-small-zh-v1.5",
    "embedding_revision": "7999e1d3359715c523056ef9478215996d62a620",
}


def test_resolve_snapshot_filtered_download_offline_run_same_path(tmp_path: Path) -> None:
    """同一条真实路径：完整仓库树清单已知、本地缓存仅六个获准文件 →
    实际 _resolve_snapshot 以与下载完全相同的 revision + patterns 解析成功。"""
    hub = _fake_hub(tmp_path, list(FILTERED_ALLOW_PATTERNS))
    path, commit = _resolve_snapshot("BAAI/bge-reranker-base", REPO_SHA, _snapshot_download=hub)
    assert commit == REPO_SHA
    assert path.name == REPO_SHA
    for name in FILTERED_ALLOW_PATTERNS:
        assert (path / name).is_file()


def test_resolve_snapshot_missing_required_file_hard_fails(tmp_path: Path) -> None:
    """过滤快照缺任一必需文件 → 硬失败（缓存子集不完整即拒绝运行）。"""
    for missing in FILTERED_ALLOW_PATTERNS:
        cached = [f for f in FILTERED_ALLOW_PATTERNS if f != missing]
        hub = _fake_hub(tmp_path / missing.replace("/", "_"), cached)
        with pytest.raises(RuntimeError, match="incomplete"):
            _resolve_snapshot("BAAI/bge-reranker-base", REPO_SHA, _snapshot_download=hub)


def test_resolve_snapshot_revision_mismatch_hard_fails(tmp_path: Path) -> None:
    """快照目录名与 pinned repo_sha 不一致 → 硬失败（不得静默换快照）。"""
    other_sha = "d" * 40
    hub = _fake_hub(tmp_path, list(FILTERED_ALLOW_PATTERNS), snapshot_name=other_sha)
    with pytest.raises(RuntimeError, match="revision mismatch"):
        _resolve_snapshot("BAAI/bge-reranker-base", REPO_SHA, _snapshot_download=hub)


def test_resolve_snapshot_refuses_non_pinned_revision(tmp_path: Path) -> None:
    """revision 不固定（None / 分支名）→ 拒绝，绝不回退到 main（hub 不被调用）。"""
    for bad in (None, "main", "master", "shortsha123"):
        def hub(**kwargs):  # must never be reached
            raise AssertionError("hub called without a pinned 40-hex revision")
        with pytest.raises(RuntimeError, match="no fallback to main"):
            _resolve_snapshot("BAAI/bge-reranker-base", bad, _snapshot_download=hub)


def test_resolve_snapshot_refuses_missing_cached_model(tmp_path: Path) -> None:
    """模型不在本地缓存（hub 报错）→ 硬失败，绝不下载。"""
    def missing(**kwargs):
        raise FileNotFoundError("not in cache")
    with pytest.raises(RuntimeError, match="download NOT authorized"):
        _resolve_snapshot("BAAI/bge-reranker-base", REPO_SHA, _snapshot_download=missing)


def test_run_refuses_unpinned_revision(tmp_path: Path) -> None:
    """E1 配置缺/坏 repo_sha → 廉价门即拒绝（不构造任何 client/embedder/scorer）。"""
    config = json.loads(
        (ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001" / "E1_CONFIG.json")
        .read_text(encoding="utf-8")
    )
    config["model_download"]["repo_sha"] = "main"  # branch ref: not a pinned sha
    tampered = tmp_path / "E1_CONFIG_tampered.json"
    tampered.write_text(json.dumps(config), encoding="utf-8")

    args = _runner_args(tmp_path)
    args.e1_config = tampered

    def client_factory(**kwargs):
        raise AssertionError("client constructed despite unpinned revision")

    with pytest.raises(SystemExit, match="no fallback to main"):
        run(args, client_factory=client_factory)


def test_embedding_problems_counterexamples() -> None:
    """运行时 embedding ID/revision 与基线配置不一致 → 反例各报一条；一致 → 空。"""
    matching = SimpleNamespace(
        model_id="BAAI/bge-small-zh-v1.5",
        model_revision="7999e1d3359715c523056ef9478215996d62a620",
    )
    assert _embedding_problems(BASELINE_UNCHANGED, matching) == []

    drifted_id = SimpleNamespace(
        model_id="BAAI/bge-base-zh-v1.5",
        model_revision="7999e1d3359715c523056ef9478215996d62a620",
    )
    problems = _embedding_problems(BASELINE_UNCHANGED, drifted_id)
    assert len(problems) == 1 and "model drifted" in problems[0]

    drifted_revision = SimpleNamespace(
        model_id="BAAI/bge-small-zh-v1.5",
        model_revision="deadbeef",
    )
    problems = _embedding_problems(BASELINE_UNCHANGED, drifted_revision)
    assert len(problems) == 1 and "revision drifted" in problems[0]

    both = SimpleNamespace(model_id="x", model_revision="y")
    assert len(_embedding_problems(BASELINE_UNCHANGED, both)) == 2


def test_control_round_run_id_is_independent() -> None:
    """控制轮独立 ID：同日期同轮次下 control 与 rerank 不共 ID，且都以 diagnostic- 前缀。"""
    rerank_r1 = _compose_run_id("20261006", disable_rerank=False, round_number=1)
    control_r1 = _compose_run_id("20261006", disable_rerank=True, round_number=1)
    assert rerank_r1 != control_r1
    assert rerank_r1.startswith("diagnostic-exp-e1-rerank-20261006-r1")
    assert control_r1.startswith("diagnostic-exp-e1-control-20261006-r1")
    # rounds never collide with each other either
    assert rerank_r1 != _compose_run_id("20261006", disable_rerank=False, round_number=2)


def test_existing_run_dir_refused(tmp_path: Path) -> None:
    """run 目录已存在 → 运行前拒绝（ID 不复用不覆盖）。"""
    existing = tmp_path / "diagnostic-exp-e1-rerank-20261006-r1"
    existing.mkdir()
    with pytest.raises(SystemExit, match="already exists"):
        _refuse_existing_run_dir(existing)


def test_missing_run_dir_allowed(tmp_path: Path) -> None:
    """目录不存在 → 放行（无副作用）。"""
    _refuse_existing_run_dir(tmp_path / "diagnostic-exp-e1-rerank-20261006-r1")


def _runner_args(tmp_path: Path) -> SimpleNamespace:
    """Real dataset + real E1 config (sha bound): gets the runner through the
    cheap offline gates so the end-to-end test reaches the directory gate."""
    return SimpleNamespace(
        dataset=ROOT / "eval" / "golden_v2.jsonl",
        output_root=tmp_path,
        e1_config=ROOT
        / "eval"
        / "runs"
        / "diagnostic-diag-baseline-20261001"
        / "E1_CONFIG.json",
        round=1,
        disable_rerank=False,
        qdrant_url="http://127.0.0.1:9",
        timeout=1.0,
        limit=10,
        prefetch_limit=20,
    )


def test_existing_run_dir_end_to_end_zero_embed_search_scorer(tmp_path: Path) -> None:
    """端到端反例：run 目录已存在 → run() 在任何 Qdrant 查询、任何模型加载、
    任何 scorer 构建之前拒绝——client/embedder/scorer 调用数全为 0。"""
    args = _runner_args(tmp_path)
    run_id = _compose_run_id(
        datetime.now(UTC).strftime("%Y%m%d"), disable_rerank=False, round_number=1
    )
    (tmp_path / run_id).mkdir()

    calls = {"client": 0, "embedder": 0, "scorer": 0}

    def client_factory(**kwargs):  # construction itself would be the first side effect
        calls["client"] += 1
        raise AssertionError("client constructed despite existing run dir")

    def embedder_factory():
        calls["embedder"] += 1
        raise AssertionError("embedder loaded despite existing run dir")

    def scorer_builder(config):
        calls["scorer"] += 1
        raise AssertionError("scorer built despite existing run dir")

    with pytest.raises(SystemExit, match="already exists"):
        run(
            args,
            client_factory=client_factory,
            embedder_factory=embedder_factory,
            scorer_builder=scorer_builder,
        )
    assert calls == {"client": 0, "embedder": 0, "scorer": 0}


def test_missing_run_dir_gate_passes_then_stops_at_first_boundary(
    tmp_path: Path,
) -> None:
    """对照：目录不存在 → 目录门放行，run() 前进到下一个门边界（client 工厂）。"""
    args = _runner_args(tmp_path)

    def client_factory(**kwargs):
        raise RuntimeError("sentinel: first boundary reached")

    with pytest.raises(RuntimeError, match="sentinel"):
        run(args, client_factory=client_factory)
