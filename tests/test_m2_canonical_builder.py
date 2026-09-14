import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.canonical_builder import build_canonical_sample_corpus


ROOT = Path(__file__).resolve().parents[1]


class M2CanonicalBuilderContractTest(unittest.TestCase):
    def test_route_ledger_builds_traceable_sample_documents_and_publish_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_canonical_sample_corpus(
                ROOT,
                Path(directory),
                artifact_prefix="canonical-contract",
            )

            self.assertEqual(result["summary"]["document_count"], 8)
            self.assertEqual(result["summary"]["page_count"], 40)
            pages = {
                page["sample_id"]: page
                for document in result["documents"]
                for page in document["pages"]
            }

            self.assertTrue(pages["M2-P008"]["publishable"])
            self.assertEqual(pages["M2-P008"]["extraction_route"], "native")
            self.assertFalse(pages["M2-P024"]["publishable"])
            self.assertEqual(pages["M2-P024"]["decision_status"], "quarantine")
            self.assertEqual(pages["M2-P004"]["text"], "")
            self.assertEqual(pages["M2-P004"]["elements"], [])

            p001_cells = [
                cell["text"] for table in pages["M2-P001"]["tables"] for cell in table["cells"]
            ]
            self.assertIn("中雨", p001_cells)
            self.assertIn("10.0~24.9", p001_cells)
            p036_cells = [
                cell["text"] for table in pages["M2-P036"]["tables"] for cell in table["cells"]
            ]
            self.assertEqual(pages["M2-P036"]["parser_name"], "docling-rapidocr-pypdfium")
            self.assertIn("管网竣工数据资料", p036_cells)

            for page in pages.values():
                for element in page["elements"]:
                    if element["bbox"] is not None:
                        x0, y0, x1, y1 = element["bbox"]
                        self.assertLessEqual(0, x0)
                        self.assertLessEqual(x1, page["width"])
                        self.assertLessEqual(0, y0)
                        self.assertLessEqual(y1, page["height"])

            output_path = Path(directory) / "canonical-contract-documents.jsonl"
            saved = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(saved), 8)
            self.assertTrue((Path(directory) / "canonical-contract-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
