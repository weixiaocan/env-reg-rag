"""Guard tests for the S2 target-evidence audit tooling (pure logic only).

The S2 round found NO engineering-fixable root cause (measured: admission is
blocked by explicit human-review statuses, ranking by RRF vote structure);
these tests pin the audit's classification logic so the delivered judgments
are reproducible.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.s2_target_evidence import (  # noqa: E402
    _classify_identity_mismatch,
    _compact,
    _fact_reachability,
    _fact_support_in_text,
    _index_elements,
    _numbers,
)


class TestIdentityMismatchClassification:
    """trace 身份对账的失配分级（G045 反例先行）：服务端 RRF tie-break
    非严格稳定，两种噪声不得计入真实失配——boundary_rank_swap（失配 chunk
    两侧都在 top-10，并列换位）与 boundary_tie_flip（失配 chunk 在 @10 截断
    线翻进/翻出，其分数等于两侧截断分，G045 ev0 chunk_5196…/chunk_54ab…
    同为 0.166667）；只有截断分之上的在位变化或可引用性变化才是 material。
    原口径把 G045 并列翻转算成真实漂移，导致 run 间 0/1 翻转（MD 写 0、
    JSON 列 G045[0] 的矛盾）。"""

    @staticmethod
    def _hits(scores: dict[str, float]) -> list[dict]:
        return [{"chunk_id": c, "score": s} for c, s in scores.items()]

    def _top(self, scores: dict[str, float]) -> list[dict]:
        # descending like evaluate_case hits
        return self._hits(dict(sorted(scores.items(), key=lambda kv: -kv[1])))

    def test_tied_boundary_rank_swap_is_not_material(self):
        # rank 9<->10 swap between chunk_8/chunk_9, both in BOTH top-10s.
        top = self._top({f"chunk_{i}": 1.0 - i * 0.1 for i in range(10)})
        verdict = _classify_identity_mismatch(
            live_id={"matched_chunk_id": "chunk_8", "match_rank": 9,
                     "citable_chunk_id": None, "citable_match_rank": None},
            trace_id={"matched_chunk_id": "chunk_9", "match_rank": 10,
                      "citable_chunk_id": None, "citable_match_rank": None},
            live_hits=top,
            trace_hits=top,
        )
        assert verdict == "boundary_rank_swap"

    def test_boundary_tie_flip_is_not_material(self):
        # G045 ev0 pattern: chunk_5196 (trace) vs chunk_54ab (live) flip
        # across the @10 cutoff; both scored 0.166667 == both cutoffs.
        scores = {"chunk_a": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                  "chunk_d": 0.2, "chunk_5196": 0.166667, "chunk_54ab": 0.166667}
        live = self._top({"chunk_a": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                          "chunk_d": 0.2, "chunk_54ab": 0.166667,
                          "chunk_e": 0.166667})
        trace = self._top({"chunk_a": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                           "chunk_d": 0.2, "chunk_5196": 0.166667,
                           "chunk_f": 0.166667})
        verdict = _classify_identity_mismatch(
            live_id={"matched_chunk_id": None, "match_rank": None,
                     "citable_chunk_id": None, "citable_match_rank": None},
            trace_id={"matched_chunk_id": "chunk_5196", "match_rank": 10,
                      "citable_chunk_id": None, "citable_match_rank": None},
            live_hits=live,
            trace_hits=trace,
        )
        assert verdict == "boundary_tie_flip"

    def test_presence_change_above_cutoff_is_material(self):
        # live matched chunk score 0.5 >> cutoff 0.166667 but absent from
        # the trace top-10 -> real drift.
        live = self._top({"chunk_new": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                          "chunk_d": 0.2, "chunk_54ab": 0.166667,
                          "chunk_e": 0.166667})
        trace = self._top({"chunk_2": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                           "chunk_d": 0.2, "chunk_5196": 0.166667,
                           "chunk_f": 0.166667})
        verdict = _classify_identity_mismatch(
            live_id={"matched_chunk_id": "chunk_new", "match_rank": 1,
                     "citable_chunk_id": None, "citable_match_rank": None},
            trace_id={"matched_chunk_id": "chunk_2", "match_rank": 1,
                      "citable_chunk_id": None, "citable_match_rank": None},
            live_hits=live,
            trace_hits=trace,
        )
        assert verdict == "material"

    def test_citable_status_flip_is_material(self):
        # same matched chunk, but citable identity appears only on one side
        # and is absent from the other side's top-10 -> material.
        live = self._top({"chunk_0": 0.5, "chunk_new": 0.333333, "chunk_c": 0.25,
                          "chunk_d": 0.2, "chunk_54ab": 0.166667,
                          "chunk_e": 0.166667})
        trace = self._top({"chunk_0": 0.5, "chunk_b": 0.333333, "chunk_c": 0.25,
                           "chunk_d": 0.2, "chunk_5196": 0.166667,
                           "chunk_f": 0.166667})
        verdict = _classify_identity_mismatch(
            live_id={"matched_chunk_id": "chunk_0", "match_rank": 1,
                     "citable_chunk_id": "chunk_new", "citable_match_rank": 2},
            trace_id={"matched_chunk_id": "chunk_0", "match_rank": 1,
                      "citable_chunk_id": None, "citable_match_rank": None},
            live_hits=live,
            trace_hits=trace,
        )
        assert verdict == "material"

    def test_identical_identity_is_none(self):
        top = self._top({f"chunk_{i}": 1.0 - i * 0.1 for i in range(10)})
        verdict = _classify_identity_mismatch(
            live_id={"matched_chunk_id": "chunk_0", "match_rank": 1,
                     "citable_chunk_id": "chunk_1", "citable_match_rank": 2},
            trace_id={"matched_chunk_id": "chunk_0", "match_rank": 1,
                      "citable_chunk_id": "chunk_1", "citable_match_rank": 2},
            live_hits=top,
            trace_hits=top,
        )
        assert verdict == "none"


class TestFactReachabilityRedLights:
    """红灯（G039 反例先行）：完整可达必须由生成 top-5 中**来源正确、足以
    支持整条 fact** 的 answer chunk 证明；仅数字在位单列为弱线索，不计可达。"""

    def _fake_candidates(self, chunks: list[tuple[str, str, str]]):
        return chunks

    def test_g039_numbers_only_duplicate_is_weak_clue_not_reachable(self):
        # G039 pattern: fact "图1为..." contains ONLY the number "1", which
        # appears in nearly every chunk -- the old criteria marked G039
        # 完整可达 via such number-coincidence duplicates (best rank 1).
        # New criteria: numbers-only presence is a WEAK CLUE, never a carrier.
        fact = "图1为城镇排水管网数字化通用技术的总体架构逻辑关系图"
        alts = [
            ("chunk_aaa", "其他文档.pdf", "图1为城镇排水管网数字化通用技术的总体架构逻辑关系图"),
            ("chunk_bbb", "某规范.pdf", "图6为降雨过程强度等级示意图，含数字1"),
        ]
        result = _fact_reachability(
            fact=fact,
            quote=None,
            source_doc="GBT+47884-2026.pdf",
            primary_carriers=[
                {"chunk_id": "chunk_loc", "live_usage_policy": "source_locator_only",
                 "answer_space_rank": None, "exists": True},
            ],
            alt_candidates=alts,
            answer_rank={"chunk_aaa": 1, "chunk_bbb": 2},
        )
        # the ONLY primary carrier is locator -- unreachable either way
        assert result["reachable_at_5"] is False
        # even the verbatim duplicate must NOT rescue it: wrong source doc
        assert all(c["chunk_id"] != "chunk_aaa" for c in result["carriers"])
        # numbers-only presence is recorded as weak clue, separately
        assert result["weak_clues"], "numbers-only hits must be listed as 弱线索"
        assert all(c["kind"] != "duplicate_answer_numbers_only" for c in result["carriers"])

    def test_cross_document_verbatim_duplicate_is_rejected(self):
        # Same-document requirement: a verbatim duplicate in an answer chunk
        # of a DIFFERENT document is not 来源正确 and must not count.
        fact = "径流系数取0.85~0.95"
        result = _fact_reachability(
            fact=fact,
            quote="各种屋面、混凝土或沥青路面 0.85~0.95",
            source_doc="正确文档.pdf",
            primary_carriers=[
                {"chunk_id": "chunk_loc", "live_usage_policy": "source_locator_only",
                 "answer_space_rank": None, "exists": True},
            ],
            alt_candidates=[
                ("chunk_cross", "错误文档.pdf", "各种屋面、混凝土或沥青路面0.85~0.95"),
                ("chunk_right", "正确文档.pdf", "……各种屋面、混凝土或沥青路面0.85~0.95……"),
            ],
            answer_rank={"chunk_cross": 1, "chunk_right": 4},
        )
        assert result["reachable_at_5"] is True
        kinds = {c["chunk_id"]: c["kind"] for c in result["carriers"]}
        assert "chunk_cross" not in kinds  # rejected as 来源不正确
        assert kinds["chunk_right"] == "duplicate_answer_chunk"

    def test_source_correct_verbatim_duplicate_rescues(self):
        fact = "径流系数取0.85~0.95"
        result = _fact_reachability(
            fact=fact,
            quote="各种屋面、混凝土或沥青路面 0.85~0.95",
            source_doc="正确文档.pdf",
            primary_carriers=[
                {"chunk_id": "chunk_loc", "live_usage_policy": "source_locator_only",
                 "answer_space_rank": None, "exists": True},
            ],
            alt_candidates=[
                ("chunk_right", "正确文档.pdf", "……各种屋面、混凝土或沥青路面0.85~0.95……"),
            ],
            answer_rank={"chunk_right": 4},
        )
        assert result["reachable_at_5"] is True
        assert result["best_rank"] == 4

    def test_rank_above_5_does_not_count(self):
        fact = "径流系数取0.85~0.95"
        result = _fact_reachability(
            fact=fact,
            quote=None,
            source_doc="正确文档.pdf",
            primary_carriers=[
                {"chunk_id": "chunk_ans", "live_usage_policy": "answer_and_citation",
                 "answer_space_rank": 6, "exists": True, "supports_fact": True},
            ],
            alt_candidates=[],
            answer_rank={"chunk_ans": 6},
        )
        assert result["reachable_at_5"] is False

    def test_citable_primary_binding_marked_unsupported_does_not_count(self):
        result = _fact_reachability(
            fact="Q为雨水设计流量",
            quote=None,
            source_doc="正确文档.pdf",
            primary_carriers=[
                {"chunk_id": "chunk_condition_only",
                 "live_usage_policy": "answer_and_citation",
                 "answer_space_rank": 1, "exists": True,
                 "supports_fact": False},
            ],
            alt_candidates=[],
            answer_rank={"chunk_condition_only": 1},
        )
        assert result["reachable_at_5"] is False
        assert result["carriers"][0]["supports_fact"] is False


class TestElementIndex:
    def test_element_ids_are_document_local_not_global(self):
        # Counterexample: element_id 'e000026' collides across documents
        # (a table in one doc, plain text in others).  The index MUST key by
        # (canonical_id, element_id) so a chunk resolves the element of ITS
        # OWN document -- keying by element_id alone silently returned the
        # last document's element and misclassified root causes.
        docs = [
            {
                "canonical_id": "canonical-8e51",
                "elements": [
                    {"element_id": "e000026", "type": "table", "quality_status": "needs_review"}
                ],
            },
            {
                "canonical_id": "canonical-3e34",
                "elements": [
                    {"element_id": "e000026", "type": "text", "quality_status": "passed"}
                ],
            },
        ]
        import json
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for doc in docs:
                (tmp_dir / f"{doc['canonical_id']}.canonical.json").write_text(
                    json.dumps(doc), encoding="utf-8"
                )
            index = _index_elements(tmp_dir)
        assert index[("canonical-8e51", "e000026")]["type"] == "table"
        assert index[("canonical-3e34", "e000026")]["type"] == "text"
        assert len(index) == 2


class TestFactSupportInText:
    def test_strong_verbatim_quote(self):
        strong, weak = _fact_support_in_text(
            "适用条件：地面汇水距离不大于 90m",
            "适用条件：地面汇水距离不大于90m",
            "……适用条件：地面汇水距离不大于90m（含其他）",
        )
        assert strong is True
        assert weak is False

    def test_strong_fact_text_without_quote(self):
        strong, weak = _fact_support_in_text("径流系数取0.85~0.95", None, "径流系数取0.85~0.95")
        assert strong is True
        assert weak is False

    def test_weak_numbers_only(self):
        # fact text is a paraphrase (not verbatim) but all numbers present
        strong, weak = _fact_support_in_text(
            "暴雨日降雨量50.0~99.9 mm（概要措辞不同）",
            None,
            "表格行：暴雨日降雨量50.0~99.9mm，其他说明文字",
        )
        assert strong is False
        assert weak is True

    def test_none_when_numbers_missing(self):
        strong, weak = _fact_support_in_text("汇水距离不大于90m", None, "汇水距离不大于150m")
        assert strong is False
        assert weak is False

    def test_pipe_cell_separator_downgrades_to_weak(self):
        # G035[1] pattern: quote window is space-joined, live chunk joins
        # table cells with '|', so the quote is NOT verbatim even after
        # whitespace compaction -- the numbers-only WEAK path is what keeps
        # this case reachable.  This pins the audit's rendering-window
        # classification (引文不逐字、数值在位), NOT content loss.
        quote = "各种屋面、混凝土或沥青路面 0.85~0.95"
        chunk = "表4.1.8-1径流系数 地面种类 | 径流系数 各种屋面、混凝土或沥青路面 | 0.85~0.95"
        strong, weak = _fact_support_in_text("屋面径流系数0.85~0.95", quote, chunk)
        assert strong is False
        assert weak is True


class TestNumberExtraction:
    def test_range_values(self):
        assert _numbers("0.85~0.95") == ["0.85~0.95"]
        assert _numbers("50.0~99.9 mm") == ["50.0~99.9"]

    def test_multiple_and_integers(self):
        assert _numbers("70%以上，97.5%") == ["70", "97.5"]

    def test_empty(self):
        assert _numbers("无数字文本") == []


class TestCompact:
    def test_removes_all_whitespace(self):
        assert _compact("a b\r\nc\td") == "abcd"
