from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from src.adapters.qdrant_evidence_retriever import QdrantEvidenceRetriever


def hit(*, evidence_id: str, usage_policy: str):
    return SimpleNamespace(
        chunk_id=evidence_id,
        primary_evidence_id=evidence_id,
        text="测试证据",
        document_version_id="doc_1",
        file_name="测试.pdf",
        physical_pages=[1],
        heading_path=[],
        usage_policy=usage_policy,
        source_uri="",
        jurisdiction="全国",
        document_kind="standard",
        effective_status="unknown",
        source_authority="",
        publication_date="",
        effective_from="",
        effective_to="",
    )


class FakeIndex:
    def __init__(self):
        self.filters = None

    def search(self, query, *, mode, filters, limit):
        self.filters = filters
        return [
            hit(evidence_id="ev_answer", usage_policy="answer_and_citation"),
            hit(evidence_id="ev_locator", usage_policy="source_locator_only"),
        ]


class FakeEmbedder:
    def embed_query(self, text):
        return [1.0]


class AnswerCorpusPolicyTest(unittest.TestCase):
    def test_fixed_answer_policy_is_sent_to_index_and_enforced_after_search(self):
        index = FakeIndex()
        retriever = QdrantEvidenceRetriever(
            index=index,
            embedder=FakeEmbedder(),
            fixed_filters={"usage_policy": "answer_and_citation"},
        )

        pack = asyncio.run(
            retriever.retrieve(
                question="要求是什么",
                resolved_scope={},
                corpus_version="corpus-test",
            )
        )

        self.assertEqual(index.filters, {"usage_policy": "answer_and_citation"})
        self.assertEqual([item.evidence_id for item in pack.evidence], ["ev_answer"])


class FocusedQueryTest(unittest.TestCase):
    def test_definition_variant_promotes_a_term_definition(self):
        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                self.calls.append((query.text, mode, filters))
                if query.text == "内涝 术语 定义":
                    definition = hit(evidence_id="ev_definition", usage_policy="answer_and_citation")
                    definition.text = "2.1.1 内涝：一定范围内的强降雨导致地面积水。"
                    return [hit(evidence_id="ev_other", usage_policy="answer_and_citation"), definition]
                return [hit(evidence_id=f"ev_base_{n}", usage_policy="answer_and_citation")
                        for n in range(10)]

        index = Index()
        index.calls = []
        pack = asyncio.run(QdrantEvidenceRetriever(index=index, embedder=FakeEmbedder()).retrieve(
            question="《城镇内涝防治技术规范》如何定义“内涝”？",
            resolved_scope={}, corpus_version="corpus-test"))
        self.assertIn("ev_definition", [item.evidence_id for item in pack.evidence[:5]])
        self.assertNotIn("ev_other", [item.evidence_id for item in pack.evidence[:5]])
        self.assertTrue(all(mode == "rrf" for _, mode, _ in index.calls))

    def test_multi_standard_comparison_keeps_one_hit_per_standard(self):
        standards = ["GB/T 28592", "T/CMSA 0013", "QX/T 489"]

        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                self.calls.append((query.text, mode))
                for n, standard in enumerate(standards):
                    if query.text.startswith(standard):
                        result = hit(evidence_id=f"ev_standard_{n}", usage_policy="answer_and_citation")
                        result.standard_number = standard + "-2019"
                        return [result]
                return [hit(evidence_id="ev_base", usage_policy="answer_and_citation")]

        index = Index()
        index.calls = []
        question = ("对照GB/T 28592与T/CMSA 0013：前者降雨量等级按哪两个时段划分？"
                    "后者把哪些历时划分为六个短时等级？QX/T 489—2019如何规定区域降雨过程等级？")
        pack = asyncio.run(QdrantEvidenceRetriever(index=index, embedder=FakeEmbedder()).retrieve(
            question=question, resolved_scope={}, corpus_version="corpus-test"))
        self.assertEqual({item.evidence_id for item in pack.evidence[:5]},
                         {"ev_base", "ev_standard_0", "ev_standard_1", "ev_standard_2"})
        self.assertEqual(len(index.calls), 4)

    def test_explicit_figure_reference_promotes_matching_caption(self):
        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                if query.text == "GB 50268 图4.6.3":
                    caption = hit(evidence_id="ev_caption", usage_policy="source_locator_only")
                    caption.text = "[图4.6.3 柔性管道沟槽回填部位与压实度示意图]"
                    caption.standard_number = "GB 50268-2008"
                    return [hit(evidence_id="ev_other", usage_policy="answer_and_citation"), caption]
                return [hit(evidence_id=f"ev_base_{n}", usage_policy="answer_and_citation")
                        for n in range(10)]

        pack = asyncio.run(QdrantEvidenceRetriever(index=Index(), embedder=FakeEmbedder()).retrieve(
            question="GB 50268图4.6.3的图名是什么？", resolved_scope={}, corpus_version="corpus-test"))
        self.assertEqual(pack.evidence[0].evidence_id, "ev_caption")

    def test_figure_reference_with_middle_particle_is_recognized(self):
        from src.adapters.qdrant_evidence_retriever import _focused_queries

        self.assertEqual(
            _focused_queries("T/CECS 758中图3.0.4的图名是什么？"),
            ("figure", [("3.0.4", "T/CECS 758 图3.0.4")]),
        )

    def test_named_table_promotes_only_its_exact_source(self):
        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                if query.text.startswith("“十四五”全国城市基础设施建设规划 表1"):
                    wrong = hit(evidence_id="wrong", usage_policy="answer_and_citation")
                    wrong.file_name = "其他规划.pdf"
                    wrong.text = "表1 城市污水处理率 80%"
                    right = hit(evidence_id="right", usage_policy="answer_and_citation")
                    right.file_name = "“十四五”全国城市基础设施建设规划.pdf"
                    right.text = "表1 城市污水处理率（%） 91.9 97.5"
                    return [wrong, right]
                return [hit(evidence_id=f"base{n}", usage_policy="answer_and_citation") for n in range(10)]

        question = "对照“十四五”全国城市基础设施建设规划表1与另一规划，2020年城市污水处理率是多少？"
        pack = asyncio.run(QdrantEvidenceRetriever(index=Index(), embedder=FakeEmbedder()).retrieve(
            question=question, resolved_scope={}, corpus_version="test"))
        self.assertEqual(pack.evidence[0].evidence_id, "right")

    def test_named_figure_and_preface_promote_exact_evidence(self):
        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                if "图5-1 图名" in query.text:
                    right = hit(evidence_id="figure", usage_policy="source_locator_only")
                    right.file_name = "城镇分流制排水系统雨污混接调查 征求意见稿.pdf"
                    right.text = "[图5-1 雨水管网中污水混接筛查技术路线]"
                    return [right]
                if query.text == "工作指南 前言 样板区":
                    right = hit(evidence_id="preface", usage_policy="answer_and_citation")
                    right.file_name = "推进城市生活污水管网全覆盖工作指南.pdf"
                    right.text = "建设城市污水管网全覆盖样板区"
                    return [right]
                return [hit(evidence_id="base", usage_policy="answer_and_citation")]

        retriever = QdrantEvidenceRetriever(index=Index(), embedder=FakeEmbedder())
        figure = asyncio.run(retriever.retrieve(
            question="《城镇分流制排水系统雨污混接调查》征求意见稿中，图5-1的图名是什么？",
            resolved_scope={}, corpus_version="test"))
        preface = asyncio.run(retriever.retrieve(
            question="工作指南前言要求建设什么样的样板区？", resolved_scope={}, corpus_version="test"))
        self.assertEqual(figure.evidence[0].evidence_id, "figure")
        self.assertEqual(preface.evidence[0].evidence_id, "preface")

    def test_two_explicit_methods_promote_both_formulas_from_same_standard(self):
        class Index(FakeIndex):
            def search(self, query, *, mode, filters, limit):
                if "容器体积法 流量公式" in query.text or "浮标法 流量公式" in query.text:
                    right = hit(evidence_id="volume" if "容器" in query.text else "float", usage_policy="answer_and_citation")
                    right.standard_number = "T/CECS 758-2020"
                    right.text = "[公式] Q=\\frac{V_i}{t}" if "容器" in query.text else "[公式] Q=\\frac{1}{n}A\\times L_i"
                    return [right]
                return [hit(evidence_id="base", usage_policy="answer_and_citation")]

        question = "按T/CECS 758，采用容器体积法时流量如何计算？浮标法的公式又如何？"
        pack = asyncio.run(QdrantEvidenceRetriever(index=Index(), embedder=FakeEmbedder()).retrieve(
            question=question, resolved_scope={}, corpus_version="test"))
        self.assertEqual([x.evidence_id for x in pack.evidence[:2]], ["volume", "float"])


if __name__ == "__main__":
    unittest.main()
