import unittest
from types import SimpleNamespace

from qdrant_client import QdrantClient

from src.application.retrieval_context import index_texts, retrieval_texts
from src.retrieval.qdrant_index import QdrantRetrievalIndex

RAIN = [{"label": "4", "title": "降雨量等级划分"}]
SNOW = [{"label": "5", "title": "降雪量等级划分"}]


def _text(eid, text, section, role="paragraph"):
    return {"element_id": eid, "type": "text", "role": role,
            "section_path": section, "content": {"text": text}}


CANONICAL = {
    "canonical_id": "can_rain",
    "metadata": {"title": "GB/T 28592-2012 降水量等级"},
    "elements": [
        _text("e000027", "降水量按24 h,12 h两个时间段进行划分", [{"label": "3"}]),
        _text("e000028", "4 降雨量等级划分", RAIN, role="heading"),
        _text("e000029", "降雨分为7个等级。具体划分见表1。", RAIN),
        _text("e000030", "单位为毫米", RAIN),
        {"element_id": "e000031", "type": "table", "section_path": RAIN,
         "content": {"cells": []}},
        _text("e000032", "注：取整到0.1。", RAIN),
        _text("e000033", "降雪分为7个等级。", SNOW),
        {"element_id": "e000034", "type": "figure", "section_path": SNOW,
         "content": {"caption": "图1 降雪示意"}},
    ],
}


def _chunk(element_ids, text):
    return SimpleNamespace(canonical_id="can_rain", element_ids=element_ids, text=text)


class RetrievalContextTest(unittest.TestCase):
    def test_table_is_indexed_with_its_lead_in_and_note(self):
        table = _chunk(["e000031"], "暴雨 | 50.0~99.9")

        [text] = retrieval_texts([table], CANONICAL)

        self.assertEqual(
            text,
            "降雨分为7个等级。具体划分见表1。\n单位为毫米\n注：取整到0.1。\n暴雨 | 50.0~99.9",
        )

    def test_text_and_figure_chunks_are_indexed_unchanged(self):
        prose = _chunk(["e000033"], "降雪分为7个等级。")
        figure = _chunk(["e000034"], "[图1 降雪示意]")

        self.assertEqual(
            retrieval_texts([prose, figure], CANONICAL),
            ["降雪分为7个等级。", "[图1 降雪示意]"],
        )

    def test_bm25_text_adds_document_title_and_dense_does_not(self):
        table = _chunk(["e000031"], "暴雨 | 50.0~99.9")

        dense, sparse = index_texts([table], {"can_rain": CANONICAL})

        self.assertFalse(dense[0].startswith("GB/T"))
        self.assertEqual(sparse[0], "GB/T 28592-2012 降水量等级\n" + dense[0])

    def test_index_stores_chunk_text_while_bm25_uses_sparse_text(self):
        client = QdrantClient(":memory:")
        index = QdrantRetrievalIndex(client=client, collection_name="ctx", vector_size=2)
        chunk = {"chunk_id": "chunk_a", "text": "暴雨 | 50.0~99.9",
                 "primary_evidence_id": "chunk_a", "metadata": {}}

        index.build([chunk], [[1.0, 0.0]], sparse_texts=["标题\n暴雨 | 50.0~99.9"])

        [hit] = index.find_by_chunk_ids(["chunk_a"])
        self.assertEqual(hit.text, "暴雨 | 50.0~99.9")
        with self.assertRaises(ValueError):
            index.build([chunk], [[1.0, 0.0]], sparse_texts=[])


if __name__ == "__main__":
    unittest.main()
