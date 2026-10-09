"""Fake-scorer integration tests for the E1 experiment runner wiring.

Pinned properties (user-approved E1 runner implementation):

* the fusion request asks for the FULL candidate set (``limit =
  2 * prefetch_limit = 40``, mode ``rrf``, filters ``None``) and the final
  top-10 is truncated AFTER the rerank -- a candidate buried at fusion rank
  40 that the scorer ranks first MUST surface in the final top-10;
* rerank OFF is exactly the baseline composition: the final top-10 equals
  the plain ``limit=10`` RRF result field for field;
* rerank ON leaves the candidate set, the sources (file_name / pages /
  document_version_id) and ``usage_policy`` unchanged -- only the order
  moves;
* non-finite scores (``None`` / ``nan`` / ``±inf``) hard-fail through the
  wired path, never silently sink;
* ``rerank_latency_ms`` is recorded single-column per case.

All of this runs on a deterministic fake index + fake embedder + fake
scorer: no live Qdrant, no model, no download.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.qdrant_index import HybridQuery, RetrievalHit  # noqa: E402
from src.retrieval.rerank import RerankConfig, RerankStage  # noqa: E402
from scripts.run_e1_rerank_experiment import run_cases  # noqa: E402

PREFETCH_LIMIT = 20
LIMIT = 10
CORPUS_VERSION = "corpus-0b9a1fd84e3d"


class _FakeIndex:
    """Deterministic fake of ``QdrantRetrievalIndex.search(mode='rrf')``.

    Each question maps to a fixed fusion-ordered candidate list; ``search``
    returns the first ``limit`` entries, so ``limit=40`` yields the full
    candidate set and ``limit=10`` reproduces the baseline composition.
    """

    def __init__(self, candidates_by_question: dict[str, list[RetrievalHit]]) -> None:
        self._candidates = candidates_by_question
        self.calls: list[tuple[str, str, object, int]] = []

    def search(
        self, query: HybridQuery, *, mode: str, filters: object, limit: int
    ) -> list[RetrievalHit]:
        self.calls.append((query.text, mode, filters, limit))
        return list(self._candidates[query.text])[:limit]


class _FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.0]


def _candidate(rank: int) -> RetrievalHit:
    """Fusion-rank-``rank`` candidate; policy alternates to prove it survives."""
    policy = "answer_and_citation" if rank % 2 == 0 else "source_locator_only"
    return RetrievalHit(
        chunk_id=f"chunk_{rank:04d}",
        text=f"候选文本 {rank} 号：雨水设计流量与汇水面积",
        primary_evidence_id=f"e{rank:06d}",
        physical_pages=[rank],
        usage_policy=policy,
        score=1.0 / rank,
        file_name=f"doc{rank % 3}.pdf",
        document_version_id=f"dv-{rank % 3}",
    )


def _cases(question_ids: list[str]) -> list[dict]:
    return [
        {
            "id": case_id,
            "question": f"{case_id} 的问题",
            "evidence": [
                {
                    "id": f"{case_id}-e0",
                    "quote": "绝不匹配的黄金引文字符串",
                    "doc_file": "doc0.pdf",
                    "page": 1,
                    "must_hit": True,
                }
            ],
        }
        for case_id in question_ids
    ]


def _fake_index(question_ids: list[str]) -> _FakeIndex:
    return _FakeIndex(
        {
            f"{case_id} 的问题": [_candidate(rank) for rank in range(1, 41)]
            for case_id in question_ids
        }
    )


def _question_texts(cases: list[dict]) -> list[str]:
    return [case["question"] for case in cases]


def _run(cases: list[dict], index: _FakeIndex, stage: RerankStage) -> list[dict]:
    return run_cases(
        cases,
        index=index,
        embedder=_FakeEmbedder(),
        stage=stage,
        corpus_version=CORPUS_VERSION,
        prefetch_limit=PREFETCH_LIMIT,
        limit=LIMIT,
    )


def test_rerank_off_top10_equals_baseline_composition() -> None:
    """重排关闭时最终 top-10 与基线组合逐字段一致（limit=10 的 RRF 原样结果）。"""
    question_ids = ["G001", "G002"]
    cases = _cases(question_ids)
    index = _fake_index(question_ids)
    stage = RerankStage(config=RerankConfig(), scorer=None)  # identity

    results = _run(cases, index, stage)

    assert [r["id"] for r in results] == question_ids
    for case, result in zip(cases, results):
        baseline = index.search(
            HybridQuery(text=case["question"], dense_vector=[0.0]),
            mode="rrf",
            filters=None,
            limit=LIMIT,
        )
        top10 = result["hits"]  # trace dicts, one per final hit in order
        assert [t["chunk_id"] for t in top10] == [h.chunk_id for h in baseline]
        for trace, want in zip(top10, baseline):
            assert trace["usage_policy"] == want.usage_policy
            assert trace["file_name"] == want.file_name
            assert trace["physical_pages"] == want.physical_pages


def test_rerank_on_candidate_set_sources_policy_unchanged_truncates_after() -> None:
    """重排开启时：候选集合、来源与 policy 不变；截取发生在重排之后。"""
    question_ids = ["G010"]
    cases = _cases(question_ids)
    index = _fake_index(question_ids)
    scored_per_question: dict[str, list[str]] = {}

    def scorer(question: str, text: str) -> float:
        scored_per_question.setdefault(question, []).append(text)
        # fusion rank 40 的候选排第一：截取若发生在重排之前必然丢失它
        return 1.0 if text.startswith("候选文本 40") else 0.0

    stage = RerankStage(config=RerankConfig(), scorer=scorer)
    results = _run(cases, index, stage)

    # -- the fusion request asked for the FULL candidate set, per leg 20
    (text, mode, filters, limit) = index.calls[0]
    assert mode == "rrf"
    assert filters is None
    assert limit == 2 * PREFETCH_LIMIT == 40

    # -- the candidate set fed to the scorer is exactly the 40 candidates
    #    (unchanged: no extra, no missing)
    candidates = index.search(
        HybridQuery(text=cases[0]["question"], dense_vector=[0.0]),
        mode="rrf",
        filters=None,
        limit=2 * PREFETCH_LIMIT,
    )
    assert scored_per_question[cases[0]["question"]] == [h.text for h in candidates]

    top10 = results[0]["hits"]
    assert len(top10) == LIMIT
    # -- reorder actually happened and truncation is AFTER the rerank:
    #    the fusion-rank-40 candidate the scorer ranked first must surface
    assert top10[0]["chunk_id"] == "chunk_0040"
    assert "chunk_0040" in [t["chunk_id"] for t in top10]

    # -- sources and policy are untouched: every final trace carries the
    #    same usage_policy / file_name / pages as its candidate counterpart
    #    (object identity itself is pinned by the RerankStage contract test)
    by_id = {h.chunk_id: h for h in candidates}
    for trace in top10:
        want = by_id[trace["chunk_id"]]
        assert trace["usage_policy"] == want.usage_policy
        assert trace["file_name"] == want.file_name
        assert trace["physical_pages"] == want.physical_pages


def test_non_finite_score_hard_fails_through_wired_path() -> None:
    """非有限分数（None/nan/±inf）经接线后的路径一律硬失败，绝不静默沉底。"""
    question_ids = ["G020"]
    cases = _cases(question_ids)
    for bad in (None, float("nan"), float("inf"), float("-inf")):
        index = _fake_index(question_ids)
        stage = RerankStage(
            config=RerankConfig(), scorer=lambda q, text, _bad=bad: _bad
        )
        with pytest.raises(ValueError, match="finite float"):
            _run(cases, index, stage)


def test_rerank_latency_recorded_single_column() -> None:
    """每条结果单独记录新增重排耗时（rerank_latency_ms 单列）。"""
    question_ids = ["G030"]
    cases = _cases(question_ids)
    index = _fake_index(question_ids)
    stage = RerankStage(config=RerankConfig(), scorer=lambda q, text: 0.5)
    results = _run(cases, index, stage)
    for result in results:
        assert "rerank_latency_ms" in result
        assert result["rerank_latency_ms"] >= 0.0
        assert result["metrics"]["recall_at_5"] == 0.0  # synthetic evidence, no real match


def test_final_top10_is_exactly_limit() -> None:
    """最终 top-10 恰为 limit 条：40 候选重排后截取 10。"""
    question_ids = ["G040"]
    cases = _cases(question_ids)
    index = _fake_index(question_ids)
    stage = RerankStage(config=RerankConfig(), scorer=lambda q, text: 0.0)
    results = _run(cases, index, stage)
    assert len(results[0]["hits"]) == LIMIT
