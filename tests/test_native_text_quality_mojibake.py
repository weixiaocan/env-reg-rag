import unittest
from src.ingestion.native_pdf import _native_text_quality

class NativeTextQualityMojibakeTest(unittest.TestCase):
    def test_symbol_mojibake_fails(self):
        text = "!\"# !\"# !$! $ %\" '()*+,-./0123 23 4 5 %&'!(!)' (!)'*!'*)& 56 (!)'*)(*!) 78"
        q = _native_text_quality(text)
        self.assertEqual(q["status"], "fail")
        self.assertIn("mojibake_symbol_text", q["reasons"])

    def test_normal_chinese_passes(self):
        text = "本标准规定了降雨过程等级及划分方法。本标准适用于降雨过程的预报、服务及科学研究。"
        q = _native_text_quality(text)
        self.assertEqual(q["status"], "pass")
        self.assertGreater(q["metrics"]["han_ratio"], 0.5)

if __name__ == "__main__":
    unittest.main()
