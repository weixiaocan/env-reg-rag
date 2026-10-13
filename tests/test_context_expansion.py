import unittest

from qdrant_client import QdrantClient

from src.adapters.qdrant_evidence_retriever import (
    QdrantContextExpander,
    evidence_item_from_hit,
)
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import RuleBasedScopeResolver
from src.domain.query import (
    Answer,
    Claim,
    EvidenceItem,
    QueryRequest,
    QueryStatus,
)
from src.retrieval.qdrant_index import QdrantRetrievalIndex

RAIN = [{"label": "4", "title": "降雨量等级划分"}]
SNOW = [{"label": "5", "title": "降雪量等级划分"}]
POLICIES = {"answer_and_citation", "source_locator_only"}


def _chunk(name, element_ids, types, text, section, policy="answer_and_citation"):
    return {
        "chunk_id": f"chunk_{name}",
        "text": text,
        "primary_evidence_id": f"chunk_{name}",
        "document_version_id": "doc_rain",
        "file_name": "降水量等级.pdf",
        "physical_pages": [4],
        "element_ids": element_ids,
        "element_types": types,
        "section_path": section,
        "usage_policy": policy,
    }


CHUNKS = [
    _chunk("older", ["e000028"], ["text"], "降雨量按24 h与12 h划分。", RAIN),
    _chunk("lead", ["e000029", "e000030"], ["text", "text"],
           "降雨分为7个等级，具体划分见表1。单位为毫米", RAIN),
    _chunk("table", ["e000031"], ["table"],
           "等级 | 24h降雨量\n暴雨 | 50.0~99.9", RAIN, "source_locator_only"),
    _chunk("note", ["e000032"], ["text"], "注：降雨量取整到0.1。", RAIN),
    _chunk("snow", ["e000033"], ["text"], "降雪分为7个等级，具体划分见表2。", SNOW),
]


class QdrantContextExpanderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_context_expansion",
            vector_size=2,
        )
        self.index.build(CHUNKS, [[1.0, 0.0]] * len(CHUNKS))
        self.by_id = {
            hit.chunk_id: hit
            for hit in self.index.find_by_chunk_ids([c["chunk_id"] for c in CHUNKS])
        }

    def _items(self, *names):
        return [evidence_item_from_hit(self.by_id[f"chunk_{n}"]) for n in names]

    async def test_table_brings_its_lead_in_and_short_note(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        extras = await expander.expand(self._items("table"))

        self.assertEqual([e.evidence_id for e in extras], ["chunk_lead", "chunk_note"])

    async def test_lead_in_already_in_context_stops_the_search(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        extras = await expander.expand(self._items("table", "lead"))

        # the older same-heading chunk is not pulled in instead
        self.assertEqual([e.evidence_id for e in extras], ["chunk_note"])

    async def test_text_evidence_is_not_expanded(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        self.assertEqual(await expander.expand(self._items("lead")), [])

    async def test_prose_under_another_heading_or_policy_is_not_added(self):
        expander = QdrantContextExpander(
            index=self.index, allowed_policies={"source_locator_only"}
        )

        # lead/note are answer_and_citation, not allowed here; snow is another heading
        self.assertEqual(await expander.expand(self._items("table")), [])


class _FixedExpander:
    def __init__(self, extra):
        self.extra = extra
        self.seen_context = None

    async def expand(self, context):
        self.seen_context = [item.evidence_id for item in context]
        return [self.extra]


def _evidence(evidence_id, text, policy="answer_and_citation"):
    return EvidenceItem(
        evidence_id=evidence_id,
        text=text,
        document_version_id="doc_rain",
        file_name="降水量等级.pdf",
        physical_pages=[4],
        heading_path=["4 降雨量等级划分"],
        usage_policy=policy,
        source_uri="",
    )


class ServiceContextExpansionTest(unittest.IsolatedAsyncioTestCase):
    async def test_expanded_evidence_is_citable_and_returned(self):
        retrieved = [_evidence(f"ev_{n}", f"证据{n}") for n in range(3)]
        lead = _evidence("ev_lead", "单位为毫米")
        expander = _FixedExpander(lead)
        service = QueryApplicationService(
            scope_resolver=RuleBasedScopeResolver(),
            evidence_retriever=InMemoryEvidenceRetriever(retrieved),
            answer_generator=InMemoryAnswerGenerator(
                Answer(claims=[Claim(claim_id="claim-1", text="单位为毫米。",
                                     evidence_ids=["ev_0", "ev_lead"])])
            ),
            generate_context_limit=2,
            context_expander=expander,
        )

        result = await service.execute(QueryRequest(
            request_id="q-expand", question="降雨量单位是什么？",
            conversation_context={}, corpus_version="test", caller_type="web"))

        # expansion sees only the limited context and never displaces it
        self.assertEqual(expander.seen_context, ["ev_0", "ev_1"])
        self.assertEqual(result.status, QueryStatus.ANSWERED)
        self.assertEqual([e.evidence_id for e in result.evidence],
                         ["ev_0", "ev_1", "ev_2", "ev_lead"])


if __name__ == "__main__":
    unittest.main()
