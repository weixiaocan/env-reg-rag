"""Contract tests for the E1 rerank seam and its executable config.

Pinned properties (user-approved E1 closure):

* rerank OFF is exactly the baseline composition (identity);
* rerank ON only reorders -- policy, file_name and every hit field are
  untouched (policy/source binding can never change in the stage);
* the 8 A/B-class baseline misses' carriers are indeed inside the RRF
  candidate pool (candidate depth <= 40) while the 6 C-class carriers are
  outside -- rerank can only ever touch the 8;
* the E1 config leaves every baseline field unchanged and the two
  experiment rounds carry independent run ids; model download is NOT
  authorized by the config submission.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.qdrant_index import RetrievalHit  # noqa: E402
from src.retrieval.rerank import RerankConfig, RerankStage  # noqa: E402

BASELINE_DIR = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001"
E1_CONFIG = BASELINE_DIR / "E1_CONFIG.json"


def _hit(rank: int, *, policy: str = "source_locator_only") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=f"chunk_{rank:04d}",
        text=f"candidate text {rank}",
        primary_evidence_id=f"e{rank:06d}",
        physical_pages=[rank],
        usage_policy=policy,
        score=1.0 / rank,
        file_name=f"doc{rank % 3}.pdf",
        document_version_id=f"dv-{rank % 3}",
    )


def _question() -> str:
    return "测试问题"


def test_rerank_off_is_identity_baseline_composition() -> None:
    """重排关闭时等价于基线：no scorer -> output equals input, order kept."""
    hits = [_hit(i) for i in range(1, 6)]
    stage = RerankStage(config=RerankConfig())
    assert not stage.enabled
    assert stage.rerank(_question(), hits) == hits
    # input hits untouched
    assert hits[0].chunk_id == "chunk_0001"


def test_rerank_on_only_reorders_policy_and_source_unchanged() -> None:
    """重排只改变顺序：同一批 hit 对象原样输出，policy/文档字段不得变化。"""
    hits = [
        _hit(1, policy="source_locator_only"),
        _hit(2, policy="answer_and_citation"),
        _hit(3, policy="source_locator_only"),
    ]
    before = {h.chunk_id: (h.usage_policy, h.file_name, h.document_version_id) for h in hits}
    stage = RerankStage(
        config=RerankConfig(),
        scorer=lambda q, text: float(text.split()[-1]),  # higher text number first
    )
    out = stage.rerank(_question(), hits)
    assert [h.chunk_id for h in out] == ["chunk_0003", "chunk_0002", "chunk_0001"]
    for hit in out:
        assert (hit.usage_policy, hit.file_name, hit.document_version_id) == before[hit.chunk_id]
    # same objects, not copies: identity preserved
    assert {id(h) for h in out} == {id(h) for h in hits}


def test_rerank_ties_keep_original_order_and_non_finite_hard_fail() -> None:
    hits = [_hit(i) for i in range(1, 5)]
    stage = RerankStage(
        config=RerankConfig(),
        scorer=lambda q, text: 0.5,  # all ties
    )
    assert [h.chunk_id for h in stage.rerank(_question(), hits)] == [h.chunk_id for h in hits]
    # 非有限分数硬失败: None / nan / ±inf must all raise, never sink silently.
    for bad in (None, float("nan"), float("inf"), float("-inf")):
        stage_bad = RerankStage(
            config=RerankConfig(),
            scorer=lambda q, text, _bad=bad: _bad,
        )
        with pytest.raises(ValueError, match="finite float"):
            stage_bad.rerank(_question(), hits)


def test_ab_class_carriers_in_rrf_candidate_pool_c_class_out() -> None:
    """A/B 类 8 条确在候选池（prefetch 20 -> 融合候选 ≤40），C 类 6 条在池外。"""
    probe = BASELINE_DIR / "miss_rerank_probe.json"
    if not probe.is_file():
        pytest.skip("baseline probe artifact not present")
    data = json.loads(probe.read_text(encoding="utf-8"))
    assert data["prefetch_limit"] == 20
    candidates = 2 * data["prefetch_limit"]
    ab = {("G020", 0), ("G023", 2), ("G004", 0), ("G023", 0), ("G026", 0), ("G019", 1), ("G027", 0), ("G040", 1)}
    c_class = {("G001", 0), ("G018", 0), ("G018", 2), ("G023", 3), ("G030", 1), ("G047", 0)}
    seen = {(m["case"], m["evidence_index"]): m.get("rrf_rank_pf") for m in data["misses"]}
    assert set(seen) == ab | c_class, "tracking set must be exactly the 14 baseline misses"
    for key, rrf in seen.items():
        if key in ab:
            assert rrf is not None and rrf <= candidates, f"{key} must be in the candidate pool"
        else:
            assert rrf is None, f"{key} must be OUTSIDE the candidate pool"


def test_e1_config_leaves_baseline_fields_unchanged() -> None:
    """policy/来源不变：E1 config 的 unchanged_fields 与基线 config 逐字段一致。"""
    if not E1_CONFIG.is_file():
        pytest.skip("E1 config not present")
    e1 = json.loads(E1_CONFIG.read_text(encoding="utf-8"))
    baseline = json.loads((BASELINE_DIR / "config.json").read_text(encoding="utf-8"))
    for key, value in e1["unchanged_fields"].items():
        assert key in baseline, f"unknown baseline field {key}"
        assert baseline[key] == value, f"baseline field {key} changed: {value} vs {baseline[key]}"
    assert e1["depth_semantics"]["final_top_k"] == baseline["limit"]
    assert e1["depth_semantics"]["candidate_fetch_depth_per_leg"] == baseline["prefetch_limit"]


def test_e1_config_two_rounds_independent_run_ids_and_no_model_authorization() -> None:
    if not E1_CONFIG.is_file():
        pytest.skip("E1 config not present")
    e1 = json.loads(E1_CONFIG.read_text(encoding="utf-8"))
    runs = e1["runs"]
    assert runs["rounds"] == 2
    assert runs["run_id_pattern_r1"] != runs["run_id_pattern_r2"]
    assert runs["run_id_pattern_r1"].startswith("diagnostic-exp-e1-rerank-")
    assert runs["run_id_pattern_r2"].startswith("diagnostic-exp-e1-rerank-")
    download = e1["model_download"]
    assert download["required"] is True
    assert download["hf_cache_has_reranker"] is False
    assert "NOT" in download["authorization"].upper()
    assert e1["classification"] == "diagnostic"
    assert e1["baseline_run_id"] == "diagnostic-diag-baseline-20261001"
