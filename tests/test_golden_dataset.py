"""Structural contract checks for the current diagnostic golden set."""

from __future__ import annotations

import json

import sys

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET = ROOT / "eval" / "golden_v8.jsonl"
SCHEMA = ROOT / "eval" / "schemas" / "golden_case_v3.schema.json"
CURRENT = ROOT / "data" / "registry" / "corpus-current.json"

_ALLOWED_PENDING = {
    "pdf_rendering_faithfulness",
    "weak_anchor_needs_review",
    "quote_spans_multiple_elements",
    "page_reanchored",
    "quote_not_in_single_chunk",
    "fact_coverage_needs_review",
    "reanchor_unresolved",
    "source_document_removed",
}
_QUOTE_KINDS = {"verbatim_kept", "rebuilt_element_excerpt", "native_table_row", "page_excerpt"}

class GoldenDatasetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        current = json.loads(CURRENT.read_text(encoding="utf-8"))
        cls.corpus_version = current["corpus_version"]
        canonical_dir = ROOT / "data" / "canonical" / "v2" / cls.corpus_version
        cls.documents = {}
        for path in canonical_dir.glob("*.canonical.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            cls.documents[document["source"]["file_name"]] = document
    def _rows(self) -> list[dict]:
        self.assertTrue(DATASET.is_file(), "re-anchored golden dataset is missing")
        # Byte-level newline split: payload text can carry NEL (U+0085) that
        # str.splitlines() wrongly treats as a line boundary.
        raw = DATASET.read_bytes().decode("utf-8")
        return [json.loads(line) for line in raw.split("\n") if line.strip()]

    def test_release_candidate_has_exactly_50_stable_cases(self) -> None:
        rows = self._rows()
        self.assertEqual(len(rows), 50)
        self.assertEqual([row["id"] for row in rows], [f"G{i:03d}" for i in range(1, 51)])
        self.assertTrue(all(row.get("status") in {"reviewed", "frozen"} for row in rows))

    def test_dataset_is_anchored_to_the_live_corpus(self) -> None:
        for row in self._rows():
            self.assertEqual(
                row.get("anchor_corpus"),
                self.corpus_version,
                f"{row['id']}: golden set must be re-anchored before it can be "
                "scored against the live corpus",
            )

    def test_every_case_is_well_formed_and_bound_to_the_anchor_corpus(self) -> None:
        allowed_modalities = {"正文", "表格", "公式", "图片图注"}
        allowed_representations = {"定义", "数值", "条件", "范围例外", "表格", "流程要求", "拒答"}
        allowed_challenges = {
            "同义表达", "多条件", "多证据", "跨页", "多文档", "表格",
            "相似干扰", "公式", "图片", "原文定位", "库外",
        }
        covered_documents: set[str] = set()
        refusal_count = 0
        for row in self._rows():
            marker = row["id"]
            self.assertTrue(row.get("question"), marker)
            self.assertTrue(row.get("expected_facts"), marker)
            self.assertTrue(row.get("doc_coverage"), marker)
            self.assertIn(row.get("expected_answerability"), {"answer", "correct_premise", "refuse"}, marker)
            covered_documents.update(row["doc_coverage"])
            representations = set((row.get("labels") or {}).get("repr") or [])
            challenges = set((row.get("labels") or {}).get("challenge") or [])
            self.assertTrue(representations <= allowed_representations, marker)
            self.assertTrue(challenges <= allowed_challenges, marker)
            if "拒答" in representations:
                refusal_count += 1
                self.assertIn(row.get("expected_answerability"), {"refuse", "correct_premise"}, marker)
            self.assertTrue(
                set((row.get("labels") or {}).get("modality") or []) <= allowed_modalities,
                marker,
            )

            for index, evidence in enumerate(row.get("evidence") or []):
                marker = f"{row['id']}[{index}]:{evidence.get('doc_file')}:{evidence.get('page')}"
                self.assertIn(evidence.get("doc_file"), self.documents, marker)
                self.assertIsInstance(evidence.get("page"), int, marker)
                self.assertGreaterEqual(evidence["page"], 1, marker)
                self.assertTrue(evidence.get("quote"), marker)
                self.assertTrue(evidence.get("element_ids"), marker)
                self.assertIn(evidence.get("quote_kind"), _QUOTE_KINDS, marker)
                self.assertTrue(
                    set(evidence.get("pending") or []) <= _ALLOWED_PENDING, marker
                )
                self.assertIn(
                    evidence.get("usage_policy"),
                    {"answer_and_citation", "source_locator_only"},
                    marker,
                )

        self.assertEqual(covered_documents, set(self.documents))
        self.assertGreaterEqual(refusal_count, 4)

    def test_schema_file_matches_vocabulary(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "rag-eval-golden-case-v3")
        kinds = schema["properties"]["evidence"]["items"]["properties"]["quote_kind"]["enum"]
        self.assertEqual(set(kinds), _QUOTE_KINDS)
        pending = schema["properties"]["evidence"]["items"]["properties"]["pending"]["items"]["enum"]
        self.assertEqual(set(pending), _ALLOWED_PENDING)

if __name__ == "__main__":
    unittest.main()
