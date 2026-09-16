from __future__ import annotations

import unittest

import numpy as np

from src.ingestion.ocr_pdf import PaddleTextOcrEngine


class _Result:
    json = {
        "res": {
            "rec_texts": ["第一条", "第二条"],
            "rec_scores": [0.99, 0.91],
            "rec_boxes": [[10, 20, 100, 40], [10, 50, 120, 75]],
            "rec_polys": [],
            "rec_labels": [],
        }
    }


class _Pipeline:
    def predict(self, input):
        return [_Result()]


class PaddleTextOcrEngineTest(unittest.TestCase):
    def test_maps_plain_ocr_lines_into_reading_order_blocks(self):
        engine = PaddleTextOcrEngine(pipeline=_Pipeline(), version="test")

        result = engine.predict(np.zeros((200, 300, 3), dtype=np.uint8))

        self.assertEqual(result["width"], 300)
        self.assertEqual(result["height"], 200)
        self.assertEqual(
            [block["block_content"] for block in result["parsing_res_list"]],
            ["第一条", "第二条"],
        )
        self.assertEqual(result["parsing_res_list"][1]["block_bbox"], [10, 50, 120, 75])
        self.assertEqual(result["overall_ocr_res"]["rec_scores"], [0.99, 0.91])
        self.assertEqual(result["table_res_list"], [])


if __name__ == "__main__":
    unittest.main()
