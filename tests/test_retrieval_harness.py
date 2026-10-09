"""Focused acceptance tests for the golden-set retrieval harness."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.evaluation.retrieval_harness import (
    evaluate_case,
    summarize_results,
    write_run_artifacts,
)


def _hit(
    rank: int,
    *,
    file_name: str,
    pages: list[int],
    element_ids: list[str],
    text: str,
    usage_policy: str = "answer_and_citation",
) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=f"chunk-{rank}",
        file_name=file_name,
        physical_pages=pages,
        element_ids=element_ids,
        text=text,
        score=1.0 / rank,
        usage_policy=usage_policy,
        heading_path=[],
    )


class RetrievalHarnessTests(unittest.TestCase):
    def test_element_match_and_page_quote_fallback_produce_recall_metrics(self) -> None:
        case = {
            "id": "G001",
            "question": "测试问题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 3,
                    "quote": "第一条必须命中的原文证据",
                    "element_ids": ["e1"],
                    "must_hit": True,
                },
                {
                    "doc_file": "b.pdf",
                    "page": 8,
                    "quote": "第二条通过页码和引文回退命中的证据",
                    "element_ids": ["e9"],
                    "must_hit": True,
                },
            ],
            "labels": {"repr": ["定义"], "challenge": ["多证据"]},
        }
        hits = [
            _hit(1, file_name="noise.pdf", pages=[1], element_ids=["x"], text="无关"),
            _hit(
                2,
                file_name="a.pdf",
                pages=[3],
                element_ids=["e1"],
                text="正文上下文：第一条必须命中的原文证据。",
            ),
            _hit(3, file_name="noise.pdf", pages=[2], element_ids=["y"], text="仍然无关"),
            _hit(
                4,
                file_name="b.pdf",
                pages=[8],
                element_ids=["different"],
                text="上下文：第二条通过页码和引文回退命中的证据。",
            ),
        ]

        result = evaluate_case(case, hits, latency_ms=12.5)

        self.assertEqual(result["metrics"]["required_evidence_count"], 2)
        self.assertEqual(result["metrics"]["recall_at_5"], 1.0)
        self.assertEqual(result["metrics"]["recall_at_10"], 1.0)
        self.assertEqual(result["metrics"]["mrr"], 0.5)
        self.assertEqual(result["evidence_results"][0]["match_method"], "quote_and_ids")
        self.assertEqual(result["evidence_results"][1]["match_method"], "page_quote")

    def test_id_intersection_without_page_corroboration_is_not_a_hit(self) -> None:
        """Cross-page drift: same document and intersecting IDs, wrong page."""
        case = {
            "id": "G042",
            "question": "漂移题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 11,
                    "quote": "锚定在第十一页的原文证据",
                    "element_ids": ["e1", "e2"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            # Same doc, ID intersects, but the hit lives on page 9 (the golden
            # set was anchored to a different corpus build / drifted page).
            _hit(1, file_name="a.pdf", pages=[9], element_ids=["e2"], text="锚定在第十一页的原文证据"),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertIsNone(result["evidence_results"][0]["match_rank"])
        self.assertIsNone(result["evidence_results"][0]["match_method"])
        self.assertEqual(result["metrics"]["recall_at_5"], 0.0)

    def test_same_page_same_id_unrelated_content_is_not_a_hit(self) -> None:
        """T1 red light: same page AND same element ID, unrelated text.

        Element IDs can alias different content within one corpus build
        (renumbering, assembly changes).  ID agreement -- even with page
        corroboration -- must never count as a hit without the quote
        corroborating the hit text.
        """
        case = {
            "id": "G035",
            "question": "同页同ID干扰题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 21,
                    "quote": "考虑区域降雨和地面渗透性能等因素确定雨水设计流量",
                    "element_ids": ["e719"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            # Same doc, same page, ID intersects -- but the text is entirely
            # unrelated content that merely reuses the aliased ID.
            _hit(1, file_name="a.pdf", pages=[21], element_ids=["e719"], text="检查流量仪与服务器通讯是否正常"),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertIsNone(result["evidence_results"][0]["match_rank"])
        self.assertIsNone(result["evidence_results"][0]["match_method"])
        self.assertEqual(result["metrics"]["recall_at_5"], 0.0)

    def test_quote_corroboration_with_id_agreement_reports_upgraded_method(self) -> None:
        """Same page + same ID + quote in the hit text -> quote_and_ids."""
        case = {
            "id": "G036",
            "question": "正例题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 22,
                    "quote": "考虑区域降雨和地面渗透性能等因素确定雨水设计流量",
                    "element_ids": ["e719"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            _hit(
                1,
                file_name="a.pdf",
                pages=[22],
                element_ids=["e719", "e720"],
                text="4.1.7 考虑区域降雨和地面渗透性能等因素确定雨水设计流量。",
            ),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertEqual(result["evidence_results"][0]["match_method"], "quote_and_ids")
        self.assertEqual(result["metrics"]["recall_at_5"], 1.0)

    def test_same_page_wrong_content_is_not_a_quote_hit(self) -> None:
        """Same page, but the text only shares a short boilerplate run."""
        case = {
            "id": "G043",
            "question": "同页干扰题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 4,
                    # 32 normalized chars: a full clause, not boilerplate.
                    "quote": "降水量按二十四小时和十二小时两个时间段进行等级划分",
                    "element_ids": ["e1"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            # Same page, no ID overlap, and the text merely shares the short
            # generic run "降水量按" with the quote (5 normalized chars).
            _hit(1, file_name="a.pdf", pages=[4], element_ids=["z9"], text="降水量按属地管理"),
            # Same page again, sharing a medium boilerplate run that the old
            # 8-char / 0.28 floor would have accepted.
            _hit(2, file_name="a.pdf", pages=[4], element_ids=["z8"], text="降水量按二十四小时"),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertIsNone(result["evidence_results"][0]["match_rank"])
        self.assertEqual(result["metrics"]["recall_at_5"], 0.0)

    def test_structured_table_quote_requires_real_content_corroboration(self) -> None:
        """A rebuilt table quote matches the assembled projection, not a fragment."""
        case = {
            "id": "G032",
            "question": "表格题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 6,
                    "quote": "短时暴雨 | 2.0~4.9 | 4.0~9.9 | 8.0~19.9",
                    "element_ids": ["e37"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            # Same page but only the row label and one cell: 4 normalized
            # shared chars ("短时暴雨") plus a numeric fragment -- no match.
            _hit(1, file_name="a.pdf", pages=[6], element_ids=["z1"], text="短时暴雨 2.0"),
            # Same page with the full rebuilt row: forward containment.
            _hit(
                2,
                file_name="a.pdf",
                pages=[6],
                element_ids=["z2"],
                text="表4 | 短时暴雨 | 2.0~4.9 | 4.0~9.9 | 8.0~19.9 | 备注",
            ),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertEqual(result["evidence_results"][0]["match_rank"], 2)
        self.assertEqual(result["evidence_results"][0]["match_method"], "page_quote")

    def test_corpus_binding_guard_rejects_stale_anchor_corpus(self) -> None:
        """Same-page ID aliasing across corpus builds is refused, not scored."""
        case = {
            "id": "G046",
            "question": "公式题",
            "anchor_corpus": "corpus-old",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 24,
                    "quote": "公式的可定位摘录",
                    "element_ids": ["e448"],
                    "must_hit": True,
                }
            ],
            "labels": {},
        }
        hits = [
            # Same page, ID matches, and the text really carries the quote.
            _hit(1, file_name="a.pdf", pages=[24], element_ids=["e448"], text="上下文 公式的可定位摘录 结束"),
        ]

        with self.assertRaises(ValueError):
            evaluate_case(case, hits, latency_ms=1.0, corpus_version="corpus-new")

        # Missing version must fail closed too: no silent scoring of
        # possibly-stale element IDs against an unverified corpus build.
        with self.assertRaises(ValueError):
            evaluate_case(case, hits, latency_ms=1.0, corpus_version=None)

        # Matching build: the same case scores normally.
        result = evaluate_case(
            case, hits, latency_ms=1.0, corpus_version="corpus-old"
        )
        self.assertEqual(result["metrics"]["recall_at_5"], 1.0)

    def test_locator_only_hit_on_answer_evidence_is_not_a_citable_hit(self) -> None:
        """Citable over-reporting red light (G026[0] pattern).

        Golden evidence is answer_and_citation, but the chunk that actually
        carries the quote projects to source_locator_only.  The location hit
        stands (match_rank / recall), yet the citable counters must stay at
        zero -- a locator-only chunk can never support a citable answer no
        matter what the golden annotation claims.
        """
        case = {
            "id": "G026",
            "question": "可引用性对账题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 3,
                    "quote": "厂网一体长效机制建设工作指南的原文证据",
                    "element_ids": ["e1", "e2"],
                    "must_hit": True,
                    "usage_policy": "answer_and_citation",
                }
            ],
            "labels": {},
        }
        hits = [
            _hit(
                1,
                file_name="a.pdf",
                pages=[3],
                element_ids=["e1", "e2", "e9"],
                text="正文：厂网一体长效机制建设工作指南的原文证据。",
                usage_policy="source_locator_only",
            ),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)

        self.assertEqual(result["evidence_results"][0]["match_rank"], 1)
        self.assertEqual(result["metrics"]["recall_at_5"], 1.0)
        # Location success, never citable answer support.
        self.assertEqual(result["metrics"]["citable_matched_at_5"], 0)
        self.assertEqual(result["metrics"]["citable_recall_at_5"], 0.0)
        self.assertEqual(result["metrics"]["citable_downgraded_at_5"], 1)
        self.assertEqual(result["evidence_results"][0]["hit_usage_policy"], "source_locator_only")

        # Control: the same hit projected answer_and_citation IS citable.
        control = evaluate_case(
            case,
            [
                _hit(
                    1,
                    file_name="a.pdf",
                    pages=[3],
                    element_ids=["e1", "e2"],
                    text="正文：厂网一体长效机制建设工作指南的原文证据。",
                )
            ],
            latency_ms=1.0,
        )
        self.assertEqual(control["metrics"]["citable_matched_at_5"], 1)
        self.assertEqual(control["metrics"]["citable_recall_at_5"], 1.0)
        self.assertEqual(control["metrics"]["citable_downgraded_at_5"], 0)

    def test_early_locator_hit_does_not_shadow_later_citable_hit(self) -> None:
        """First-hit shadowing red light.

        A locator-only chunk corroborates the quote at rank 2 and an
        answer_and_citation chunk corroborates it at rank 7.  The matcher
        must keep scanning after the first hit: citable @5 is a downgrade
        (no citable corroboration within 5), citable @10 counts at the
        citable hit's own rank -- the early locator hit shadows nothing.
        """
        case = {
            "id": "G019",
            "question": "遮蔽题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 28,
                    "quote": "单个混接点或混接源的混接程度分级标准应按表",
                    "element_ids": ["e544"],
                    "must_hit": True,
                    "usage_policy": "answer_and_citation",
                }
            ],
            "labels": {},
        }
        noise = [
            _hit(rank, file_name="noise.pdf", pages=[1], element_ids=["x"], text="无关")
            for rank in (1, 3, 4, 5, 6, 8, 9, 10)
        ]
        hits = [
            noise[0],
            _hit(
                2,
                file_name="a.pdf",
                pages=[28],
                element_ids=["e546"],
                text="表7.2.2 单个混接点或混接源的混接程度分级标准应按表 有关规定 混接程度与分级",
                usage_policy="source_locator_only",
            ),
            *noise[1:5],
            _hit(
                7,
                file_name="a.pdf",
                pages=[28],
                element_ids=["e544", "e545"],
                text="7.2.2 单个混接点或混接源的混接程度分级标准应按表7.2.2 的规定确定。",
                usage_policy="answer_and_citation",
            ),
            *noise[5:],
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)
        item = result["evidence_results"][0]

        # First any-hit stays the locator chunk (rank 2)...
        self.assertEqual(item["match_rank"], 2)
        self.assertEqual(item["hit_usage_policy"], "source_locator_only")
        # ...but the first citable hit is independently recorded at rank 7.
        self.assertEqual(item["citable_match_rank"], 7)
        self.assertEqual(item["locator_match_rank"], 2)

        metrics = result["metrics"]
        self.assertEqual(metrics["recall_at_5"], 1.0)
        # @5: matched but no citable corroboration within 5 -> downgrade.
        self.assertEqual(metrics["citable_matched_at_5"], 0)
        self.assertEqual(metrics["citable_downgraded_at_5"], 1)
        # @10: the citable hit at rank 7 counts at its own rank.
        self.assertEqual(metrics["citable_matched_at_10"], 1)
        self.assertEqual(metrics["citable_downgraded_at_10"], 0)
        self.assertEqual(metrics["citable_recall_at_10"], 1.0)

    def test_early_citable_hit_outranks_later_locator_hit(self) -> None:
        """Reverse order: answer chunk first, locator chunk later."""
        case = {
            "id": "G019",
            "question": "反向遮蔽题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 28,
                    "quote": "单个混接点或混接源的混接程度分级标准应按表",
                    "element_ids": ["e544"],
                    "must_hit": True,
                    "usage_policy": "answer_and_citation",
                }
            ],
            "labels": {},
        }
        hits = [
            _hit(
                1,
                file_name="a.pdf",
                pages=[28],
                element_ids=["e544", "e545"],
                text="7.2.2 单个混接点或混接源的混接程度分级标准应按表7.2.2 的规定确定。",
                usage_policy="answer_and_citation",
            ),
            _hit(
                2,
                file_name="a.pdf",
                pages=[28],
                element_ids=["e546"],
                text="表7.2.2 单个混接点或混接源的混接程度分级标准应按表 有关规定 混接程度与分级",
                usage_policy="source_locator_only",
            ),
        ]

        result = evaluate_case(case, hits, latency_ms=1.0)
        item = result["evidence_results"][0]
        metrics = result["metrics"]

        self.assertEqual(item["match_rank"], 1)
        self.assertEqual(item["hit_usage_policy"], "answer_and_citation")
        self.assertEqual(item["citable_match_rank"], 1)
        self.assertEqual(item["locator_match_rank"], 2)
        self.assertEqual(metrics["citable_matched_at_5"], 1)
        self.assertEqual(metrics["citable_matched_at_10"], 1)
        self.assertEqual(metrics["citable_downgraded_at_5"], 0)
        self.assertEqual(metrics["citable_downgraded_at_10"], 0)
        self.assertEqual(metrics["citable_recall_at_5"], 1.0)

    def test_g019_mixed_policy_chunks_regression(self) -> None:
        """G019[1] regression: its quote is corroborated by BOTH the page-28
        body chunk (answer_and_citation) and the page-28 table-7.2.2 chunk
        (source_locator_only).  Whichever ranks first, the citable metric
        must find the answer chunk within the same K."""
        quote = (
            "181 的有关规定 > 7.2 评估指标与方法 > 7.2.1 单个氓接点或提接源的1昆接程度可依据接入管管径、混接\n"
            "7. 2. 1\n"
            "单个氓接点或提接源的1昆接程度可依据接入管管径、混接\n"
            "水量、1昆接水质进行评估。\n"
            "7.2.2\n"
            "单个混接点或泪接源的海接程度分级标准应按表7. 2. 2"
        )
        body_text = (
            "181 的有关规定 > 7.2 评估指标与方法 > 7.2.1 单个氓接点或提接源的1昆接程度可依据接入管管径、混接\n"
            "7. 2. 1\n"
            "单个氓接点或提接源的1昆接程度可依据接入管管径、混接\n"
            "水量、1昆接水质进行评估。\n"
            "7.2.2\n"
            "单个混接点或泪接源的海接程度分级标准应按表7. 2. 2"
        )
        table_text = (
            "181 的有关规定 > 7.2 评估指标与方法 > 7.2.1 单个氓接点或提接源的1昆接程度可依据接入管管径、混接"
            " | 表7. 2. 2  | 单个混接点或混接源混接程度分级标准 | 混接程度与分级 | 接入管管径d (mm) | "
            "出流流量q (m³/d) | 污水出流水质(mg/L) | 污水出流水质(mg/L) | 混接程度与分级 | 接入管管径d (mm) | "
            "出流流量q (m³/d) | 氨氮 | COD | 重度混接(3级) | d≥600 | q>600 | >30 | >200"
        )
        case = {
            "id": "G019",
            "question": "混接程度评估指标的回归题",
            "evidence": [
                {
                    "doc_file": "T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf",
                    "page": 28,
                    "quote": quote,
                    "element_ids": ["e000544", "e000545"],
                    "must_hit": True,
                    "usage_policy": "answer_and_citation",
                }
            ],
            "labels": {},
        }

        def _body(rank: int, policy: str):
            return _hit(
                rank,
                file_name="T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf",
                pages=[28],
                element_ids=["e000544", "e000545"],
                text=body_text,
                usage_policy=policy,
            )

        def _table(rank: int, policy: str):
            return _hit(
                rank,
                file_name="T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf",
                pages=[28],
                element_ids=["e000546"],
                text=table_text,
                usage_policy=policy,
            )

        # Table (locator-only) ranked FIRST -- the shadowing scenario.
        result = evaluate_case(case, [_table(1, "source_locator_only"), _body(2, "answer_and_citation")], latency_ms=1.0)
        item = result["evidence_results"][0]
        self.assertEqual(item["match_rank"], 1)
        self.assertEqual(item["hit_usage_policy"], "source_locator_only")
        self.assertEqual(item["citable_match_rank"], 2)
        self.assertEqual(result["metrics"]["citable_matched_at_5"], 1)
        self.assertEqual(result["metrics"]["citable_downgraded_at_5"], 0)
        self.assertEqual(result["metrics"]["citable_recall_at_5"], 1.0)

        # Body (answer) ranked first -- the plain scenario.
        result = evaluate_case(case, [_body(1, "answer_and_citation"), _table(2, "source_locator_only")], latency_ms=1.0)
        item = result["evidence_results"][0]
        self.assertEqual(item["citable_match_rank"], 1)
        self.assertEqual(result["metrics"]["citable_matched_at_5"], 1)
        self.assertEqual(result["metrics"]["citable_downgraded_at_5"], 0)

        # Locator-only table is the ONLY corroborating hit: downgrade.
        result = evaluate_case(case, [_table(1, "source_locator_only")], latency_ms=1.0)
        item = result["evidence_results"][0]
        self.assertIsNone(item["citable_match_rank"])
        self.assertEqual(result["metrics"]["citable_matched_at_5"], 0)
        self.assertEqual(result["metrics"]["citable_downgraded_at_5"], 1)
        self.assertEqual(result["metrics"]["recall_at_5"], 1.0)

    def test_refusal_case_without_must_hit_is_excluded_from_retrieval_denominator(self) -> None:
        case = {
            "id": "G008",
            "question": "库外问题",
            "evidence": [
                {
                    "doc_file": "local.pdf",
                    "page": 1,
                    "quote": "本地资料标题",
                    "element_ids": ["e1"],
                    "must_hit": False,
                }
            ],
            "labels": {"repr": ["拒答"], "challenge": ["库外"]},
        }

        result = evaluate_case(case, [], latency_ms=1.0)
        summary = summarize_results([result])

        self.assertFalse(result["metrics"]["retrieval_applicable"])
        self.assertEqual(summary["eligible_case_count"], 0)
        self.assertIsNone(summary["macro"]["recall_at_5"])

    def test_run_artifacts_include_trace_summary_and_html(self) -> None:
        case = {
            "id": "G001",
            "question": "测试问题",
            "evidence": [
                {
                    "doc_file": "a.pdf",
                    "page": 3,
                    "quote": "必须命中的原文证据",
                    "element_ids": ["e1"],
                    "must_hit": True,
                }
            ],
            "labels": {"repr": ["定义"], "challenge": ["原文定位"]},
        }
        result = evaluate_case(
            case,
            [_hit(1, file_name="a.pdf", pages=[3], element_ids=["e1"], text="证据")],
            latency_ms=2.0,
        )
        summary = summarize_results([result])
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-1"
            write_run_artifacts(
                run_dir=run_dir,
                config={"run_id": "run-1", "corpus_version": "corpus-test"},
                results=[result],
                summary=summary,
            )

            self.assertTrue((run_dir / "config.json").is_file())
            self.assertTrue((run_dir / "cases" / "G001.json").is_file())
            self.assertTrue((run_dir / "summary.json").is_file())
            self.assertTrue((run_dir / "report.html").is_file())
            saved = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["case_count"], 1)


if __name__ == "__main__":
    unittest.main()
