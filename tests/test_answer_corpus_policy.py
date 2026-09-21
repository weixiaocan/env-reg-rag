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


if __name__ == "__main__":
    unittest.main()
