import unittest

from qdrant_client import QdrantClient

from src.adapters.qdrant_evidence_retriever import (
    QdrantContextExpander,
    evidence_item_from_hit,
)
from src.application.answer_generation import InMemoryAnswerGenerator
from src.application.evidence_retrieval import InMemoryEvidenceRetriever
from src.application.query_service import QueryApplicationService
from src.application.scope_resolution import PassthroughScopeResolver
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

    async def test_formula_brings_its_variable_lines_but_not_unit_fragments(self):
        clause = [{"label": "5.2.4", "title": ""}]
        chunks = [
            _chunk("f_lead", ["e000193"], ["text"], "5.2.4 应按下式计算：", clause),
            _chunk("f_formula", ["e000194", "e000195"], ["formula", "text"],
                   "[公式]\nBI=ADF-KQ_{g}\n式中：BI——旱天入流入渗量；", clause,
                   "source_locator_only"),
            _chunk("f_unit", ["e000196"], ["formula"], "[公式]\n(m³/d)", clause,
                   "source_locator_only"),
            _chunk("f_adf", ["e000197"], ["text"], "ADF——旱天日均流量监测值；", clause),
            _chunk("f_qg", ["e000199"], ["text"], "Qg——区域日平均用水量；", clause),
            _chunk("f_k", ["e000201"], ["text"], "K——折算系数，宜取0.8~0.9。", clause),
            _chunk("f_next", ["e000202"], ["text"], "5.2.5 采用夜间最小流量法…",
                   [{"label": "5.2.5", "title": ""}]),
        ]
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_formula_clause",
            vector_size=2,
        )
        index.build(chunks, [[1.0, 0.0]] * len(chunks))
        formula = index.find_by_chunk_ids(["chunk_f_formula"])[0]
        expander = QdrantContextExpander(index=index, allowed_policies=POLICIES)

        extras = await expander.expand([evidence_item_from_hit(formula)])

        self.assertEqual(
            [e.evidence_id for e in extras],
            ["chunk_f_lead", "chunk_f_adf", "chunk_f_qg", "chunk_f_k"],
        )

    async def test_long_variable_list_is_kept_but_long_prose_is_not(self):
        clause = [{"label": "5.3.6", "title": ""}]
        variables = "5.3.6\n" + "\n".join(
            f"X{i}——第{i}个变量的含义说明，按监测点上游管径不小于300mm的管段统计；" for i in range(6)
        )
        chunks = [
            _chunk("r_formula", ["e000269", "e000270"], ["formula", "text"],
                   "5.3.6\n[公式]\nQ_{rl}=\\frac{a}{b}\n式中：Q——评估值；", clause,
                   "source_locator_only"),
            _chunk("r_vars", ["e000272"], ["text"], variables, clause),
            _chunk("r_prose", ["e000273"], ["text"], "5.3.6\n" + "条文说明" * 60, clause),
        ]
        self.assertGreater(len(variables), 160)
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_variable_list",
            vector_size=2,
        )
        index.build(chunks, [[1.0, 0.0]] * len(chunks))
        formula = index.find_by_chunk_ids(["chunk_r_formula"])[0]
        expander = QdrantContextExpander(index=index, allowed_policies=POLICIES)

        extras = await expander.expand([evidence_item_from_hit(formula)])

        self.assertEqual([e.evidence_id for e in extras], ["chunk_r_vars"])

    async def test_lead_in_brings_the_formula_and_its_variable_lines(self):
        clause = [{"label": "5.3.6", "title": ""}]
        chunks = [
            _chunk("q_lead", ["e000268"], ["text"],
                   "5.3.6\n5.3.6 评估值应按下\n式计算：", clause),
            _chunk("q_formula", ["e000269", "e000270"], ["formula", "text"],
                   "5.3.6\n[公式]\nQ_{rl}=\\frac{a}{b}\n式中：Q_rl——评估值；", clause),
            _chunk("q_unit", ["e000271"], ["formula"], "5.3.6\n[公式]\n[m³/(km·", clause,
                   "source_locator_only"),
            _chunk("q_vars", ["e000272", "e000276"], ["text", "text"],
                   "5.3.6\nm——降雨场次数；\nn——污水管段数量。", clause,
                   "source_locator_only"),
        ]
        index = QdrantRetrievalIndex(
            client=QdrantClient(":memory:"),
            collection_name="test_lead_in_formula",
            vector_size=2,
        )
        index.build(chunks, [[1.0, 0.0]] * len(chunks))
        lead = index.find_by_chunk_ids(["chunk_q_lead"])[0]
        expander = QdrantContextExpander(index=index, allowed_policies=POLICIES)

        extras = await expander.expand([evidence_item_from_hit(lead)])

        self.assertEqual([e.evidence_id for e in extras], ["chunk_q_formula", "chunk_q_vars"])

    async def test_lead_in_already_in_context_stops_the_search(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        extras = await expander.expand(self._items("table", "lead"))

        # the older same-heading chunk is not pulled in instead
        self.assertEqual([e.evidence_id for e in extras], ["chunk_note"])

    async def test_plain_text_evidence_is_not_expanded(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        self.assertEqual(await expander.expand(self._items("older", "note")), [])

    async def test_lead_in_brings_the_table_it_refers_to(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        extras = await expander.expand(self._items("lead"))

        # the table's short note comes with it, as when the table ranks itself
        self.assertEqual([e.evidence_id for e in extras], ["chunk_table", "chunk_note"])

    async def test_reference_without_a_following_table_adds_nothing(self):
        expander = QdrantContextExpander(index=self.index, allowed_policies=POLICIES)

        # snow points at 表2, but no structured chunk follows under its heading
        self.assertEqual(await expander.expand(self._items("snow")), [])

    async def test_referenced_table_respects_policy(self):
        expander = QdrantContextExpander(
            index=self.index, allowed_policies={"answer_and_citation"}
        )

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
            scope_resolver=PassthroughScopeResolver(),
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
