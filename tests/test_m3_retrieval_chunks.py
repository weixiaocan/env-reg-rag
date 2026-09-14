import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.evidence_builder import build_evidence_units
from src.evaluation.retrieval_chunk_builder import build_retrieval_chunks


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
CANONICAL = ROOT / "data" / "canonical" / "m2-canonical-sample-v1-documents.jsonl"


class M3RetrievalChunkContractTest(unittest.TestCase):
    def test_chunks_add_search_context_without_replacing_citable_evidence(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = build_retrieval_chunks(ROOT, EVIDENCE, Path(first_dir))
            second = build_retrieval_chunks(ROOT, EVIDENCE, Path(second_dir))

            evidence_units = [
                json.loads(line)
                for line in EVIDENCE.read_text(encoding="utf-8").splitlines()
            ]
            evidence_ids = {unit["evidence_id"] for unit in evidence_units}
            self.assertEqual(len(first["chunks"]), len(evidence_units))
            self.assertEqual(
                [chunk["chunk_id"] for chunk in first["chunks"]],
                [chunk["chunk_id"] for chunk in second["chunks"]],
            )
            for chunk in first["chunks"]:
                self.assertEqual(len(chunk["evidence_ids"]), 1)
                self.assertIn(chunk["primary_evidence_id"], evidence_ids)
                self.assertEqual(
                    chunk["primary_evidence_id"], chunk["evidence_ids"][0]
                )

            rainfall_chunk = next(
                chunk
                for chunk in first["chunks"]
                if chunk["evidence_type"] == "table"
                and chunk["physical_pages"] == [4]
                and "中雨" in chunk["text"]
            )
            self.assertIn("GB/T 28592-2012", rainfall_chunk["text"])
            self.assertIn("10.0~24.9", rainfall_chunk["text"])
            source_unit = next(
                unit
                for unit in evidence_units
                if unit["evidence_id"] == rainfall_chunk["primary_evidence_id"]
            )
            self.assertEqual(
                source_unit["locator"]["physical_pages"],
                rainfall_chunk["physical_pages"],
            )

            output_path = Path(first_dir) / "m3-retrieval-chunks-v1.jsonl"
            self.assertTrue(output_path.exists())
            manifest = json.loads(
                (Path(first_dir) / "m3-retrieval-chunks-v1.manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["summary"]["chunk_count"], len(evidence_units))

    def test_formula_locator_chunk_preserves_source_only_usage_policy(self):
        with tempfile.TemporaryDirectory() as evidence_dir, tempfile.TemporaryDirectory() as chunk_dir:
            build_evidence_units(ROOT, CANONICAL, Path(evidence_dir))
            evidence_path = Path(evidence_dir) / "m3-evidence-units-v1.jsonl"
            result = build_retrieval_chunks(ROOT, evidence_path, Path(chunk_dir))

            locator_chunk = next(
                chunk
                for chunk in result["chunks"]
                if chunk["evidence_type"] == "source_locator"
            )
            self.assertIn("用水量折算法", locator_chunk["text"])
            self.assertEqual(
                locator_chunk["metadata"]["usage_policy"], "source_locator_only"
            )
            self.assertEqual(
                locator_chunk["metadata"]["text_reliability"],
                "unverified_automatic_extraction",
            )
            self.assertEqual(locator_chunk["physical_pages"], [19])


if __name__ == "__main__":
    unittest.main()
