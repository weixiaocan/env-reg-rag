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


if __name__ == "__main__":
    unittest.main()
