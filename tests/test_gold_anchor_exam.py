"""Grading rules of the human gold-anchor exam (acceptance criterion A1)."""

from __future__ import annotations

import unittest

from src.evaluation.gold_anchor_exam import ExamChunk, grade, structure_label


def _chunk(body, *, pages=(4,), labels=("2", "2.3", "2.3.9"), doc="规范.pdf"):
    return ExamChunk(document=doc, pages=frozenset(pages), section_labels=tuple(labels), body=body)


def _text_anchor(truth, *, page=4, path="2 基本规定 > 2.3.9", doc="规范.pdf"):
    return {"anchor_id": "TXT-X", "anchor_type": "text_or_clause", "file_name": doc,
            "pdf_page": page, "structure_path": path, "proposed_truth": truth}


class GoldAnchorExamTest(unittest.TestCase):
    def test_text_and_structure_pass_with_punctuation_and_width_noise(self):
        report = grade([_text_anchor("排水工程设施运行应建立应急体系，并应定期进行演练。")],
                       [_chunk("2.3.9 排水工程设施运行应建立应急体系,\n并应定期进行演练")])
        r = report.results[0]
        self.assertTrue(r.text_pass)
        self.assertTrue(r.structure_pass)

    def test_watermark_inside_sentence_fails_text(self):
        report = grade([_text_anchor("制定安全生产、职业卫生、环境保护")],
                       [_chunk("制定安全生产、职\n住房城乡建设部信息公并\n业卫生、环境保护")])
        self.assertFalse(report.results[0].text_pass)

    def test_wrong_section_fails_structure_only(self):
        report = grade([_text_anchor("必须执行本规范。", path="1 总则 > 1.0.2")],
                       [_chunk("必须执行本规范。", labels=("1", "1.0.1"))])
        r = report.results[0]
        self.assertTrue(r.text_pass)
        self.assertFalse(r.structure_pass)

    def test_page_window_is_plus_minus_one(self):
        report = grade([_text_anchor("必须执行本规范。", page=4)],
                       [_chunk("必须执行本规范。", pages=(7,))])
        self.assertFalse(report.results[0].text_pass)

    def test_unlabelled_structure_is_not_graded(self):
        report = grade([_text_anchor("2024年3月8日。", path="落款与网页元数据")],
                       [_chunk("2024年3月8日", labels=())])
        self.assertIsNone(report.results[0].structure_pass)

    def test_table_requires_every_number(self):
        anchor = {"anchor_id": "TAB-X", "anchor_type": "table_or_numeric", "file_name": "规范.pdf",
                  "pdf_page": 4, "structure_path": "表1 > 中雨",
                  "proposed_truth": "12 h：5.0～14.9 mm；24 h：10.0～24.9 mm。"}
        ok = grade([anchor], [_chunk("中雨 | 5.0～14.9 | 10.0～24.9 12h 24h")])
        miss = grade([anchor], [_chunk("中雨 | 5.0～14.9 | 12h 24h")])
        self.assertTrue(ok.results[0].text_pass)
        self.assertFalse(miss.results[0].text_pass)

    def test_structure_label_forms(self):
        self.assertEqual(structure_label("2.3.9"), "2.3.9")
        self.assertEqual(structure_label("（九）"), "(九)")
        self.assertEqual(structure_label("一、工作目标"), "一")
        self.assertIsNone(structure_label("落款与网页元数据"))


if __name__ == "__main__":
    unittest.main()
