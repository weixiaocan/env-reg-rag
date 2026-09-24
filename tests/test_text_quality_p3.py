import unittest

from src.ingestion.text_quality import document_text_quality, is_answerable_text, score_text


class TextQualityP3Test(unittest.TestCase):
    def test_mojibake_fails(self):
        text = '!"# !"# !$! $ %" ' * 4
        q = score_text(text)
        self.assertEqual(q["status"], "fail")
        self.assertIn("mojibake_symbol_text", q["reasons"])
        self.assertFalse(is_answerable_text(text))

    def test_chinese_passes(self):
        text = "本标准规定了降雨过程等级及划分方法。本标准适用于降雨过程的预报、服务及科学研究。"
        q = score_text(text)
        self.assertEqual(q["status"], "pass")
        self.assertTrue(is_answerable_text(text))

    def test_document_garbled_verdict(self):
        pages = []
        for i in range(1, 9):
            pages.append({
                "physical_page": i,
                "decision_status": "approved",
                "text": '!"# !"# !$! $ %" (()())' * 3,
            })
        for i in range(9, 13):
            pages.append({
                "physical_page": i,
                "decision_status": "quarantine",
                "decision_reasons": ["visual_blank_page"],
                "text": "",
            })
        report = document_text_quality({
            "file_name": "QXT-489.pdf",
            "sha256": "abc",
            "pages": pages,
            "canonical_rel_path": "data/raw/QXT-489.pdf",
            "metadata": {},
        })
        self.assertEqual(report["verdict"], "garbled_text")
        self.assertEqual(report["bad_pages"], 12)


if __name__ == "__main__":
    unittest.main()
