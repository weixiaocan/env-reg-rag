"""Contract checks for the re-anchored golden-set v2 (policy-aware)."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.v2_chunker import (  # noqa: E402
    _project_formula,
    _project_figure,
    _project_table,
    chunk_document,
)
from src.application.evidence_policy import chunk_usage_policy  # noqa: E402

DATASET = ROOT / "eval" / "golden_v2.jsonl"
SCHEMA = ROOT / "eval" / "schemas" / "golden_case_v2.schema.json"
CURRENT = ROOT / "data" / "registry" / "corpus-current.json"

_ALLOWED_PENDING = {
    "pdf_rendering_faithfulness",
    "weak_anchor_needs_review",
    "quote_spans_multiple_elements",
    "page_reanchored",
    "quote_not_in_single_chunk",
    "fact_coverage_needs_review",
}
_LOCATABLE_KINDS = {"verbatim_kept", "rebuilt_element_excerpt", "native_table_row"}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", value)


def _normalized_chunk(value: str) -> str:
    return _normalized(value or "")


def _element_text(element: dict) -> str:
    """Chunk-faithful projection of one Canonical element."""
    content = element.get("content") or {}
    kind = element.get("type")
    if kind == "text":
        return content.get("text") or ""
    if kind == "table":
        try:
            return _project_table(content)
        except KeyError:
            return ""
    if kind == "formula":
        return _project_formula(content)
    if kind == "figure":
        return _project_figure(content) or ""
    return ""


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
        # Offline-reconstructed retrieval chunks (deterministic, same
        # projection as the published build): the locatability contract is
        # "the quote is a run of a REAL chunk's text", not merely of the
        # joined element projections.
        cls.chunks = {
            file_name: [
                (
                    _normalized_chunk(chunk.text),
                    set(chunk.element_ids),
                    {
                        span.physical_page
                        for span in chunk.source_spans or []
                        if span.physical_page
                    },
                    chunk.text,
                )
                for chunk in chunk_document(document)
            ]
            for file_name, document in cls.documents.items()
        }

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
                f"{row['id']}: golden v2 must be re-anchored before it can be "
                "scored against the live corpus",
            )

    def test_every_evidence_anchor_is_locatable_in_the_anchor_corpus(self) -> None:
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
            self.assertIn(row.get("expected_answerability"), {"answer", "locate", "refuse"}, marker)
            covered_documents.update(row["doc_coverage"])
            representations = set((row.get("labels") or {}).get("repr") or [])
            challenges = set((row.get("labels") or {}).get("challenge") or [])
            self.assertTrue(representations <= allowed_representations, marker)
            self.assertTrue(challenges <= allowed_challenges, marker)
            if "拒答" in representations:
                refusal_count += 1
                self.assertEqual(row.get("expected_answerability"), "refuse", marker)
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
                self.assertIn(evidence.get("quote_kind"), _LOCATABLE_KINDS | {"page_excerpt"}, marker)
                self.assertTrue(
                    set(evidence.get("pending") or []) <= _ALLOWED_PENDING, marker
                )
                self.assertIn(
                    evidence.get("usage_policy"),
                    {"answer_and_citation", "source_locator_only"},
                    marker,
                )

                document = self.documents[evidence["doc_file"]]
                by_id = {element["element_id"]: element for element in document["elements"]}
                for element_id in evidence["element_ids"]:
                    self.assertIn(element_id, by_id, marker)

                if not evidence.get("must_hit", True):
                    # Refusal locator: page excerpts come from same-page
                    # elements -- every element sits exactly on the page.
                    self.assertEqual(evidence["quote_kind"], "page_excerpt", marker)
                    for element_id in evidence["element_ids"]:
                        pages = {
                            span.get("physical_page")
                            for span in by_id[element_id].get("source_spans") or []
                            if span.get("physical_page")
                        }
                        self.assertIn(evidence["page"], pages, f"{marker}:{element_id}")
                    continue

                # must_hit: the quote must be a contiguous run of a REAL
                # retrieval chunk's text, that chunk must cover the evidence
                # element_ids and carry the evidence page among its physical
                # pages -- otherwise no hit can ever corroborate this evidence
                # and the strict matcher would be scoring an unreachable
                # target.
                self.assertIn(evidence["quote_kind"], _LOCATABLE_KINDS, marker)
                quote = _normalized(evidence["quote"])
                self.assertTrue(quote, marker)
                self.assertGreaterEqual(len(quote), 12, marker)
                evidence_ids = set(evidence["element_ids"])
                supporting = [
                    (text, ids, chunk_pages, raw_text)
                    for text, ids, chunk_pages, raw_text in self.chunks[evidence["doc_file"]]
                    if quote in text and evidence_ids <= ids
                    and evidence["page"] in chunk_pages
                ]
                self.assertTrue(
                    supporting,
                    f"{marker}: no retrieval chunk contains the quote, covers "
                    "its element_ids and carries the evidence page",
                )
                if evidence["quote_kind"] == "rebuilt_element_excerpt":
                    # The humanized transcription must live in rendering,
                    # never in the locatable quote.
                    self.assertTrue(evidence.get("rendering"), marker)
                if evidence["quote_kind"] in {"verbatim_kept", "native_table_row"}:
                    self.assertNotIn("rendering", evidence, marker)

        self.assertEqual(covered_documents, set(self.documents))
        self.assertGreaterEqual(refusal_count, 4)

    def test_usage_policy_annotation_matches_the_carrying_chunk(self) -> None:
        """Citable over-reporting red light (G026[0] pattern).

        The usage_policy annotation must be the policy projected from the
        elements of the chunk that actually carries the quote -- not from a
        subset of that chunk's elements.  A golden row annotated
        answer_and_citation whose carrying chunk is source_locator_only would
        inflate citable metrics whenever the annotation, instead of the hit,
        decides citability.
        """
        by_file_elements = {
            file_name: {element["element_id"]: element for element in document["elements"]}
            for file_name, document in self.documents.items()
        }
        mismatches: list[str] = []
        for row in self._rows():
            for index, evidence in enumerate(row.get("evidence") or []):
                if not evidence.get("must_hit", True):
                    continue
                quote = _normalized(evidence["quote"])
                evidence_ids = set(evidence["element_ids"])
                supporting = [
                    (ids, raw_text)
                    for _text, ids, _pages, raw_text in self.chunks[evidence["doc_file"]]
                    if quote in _text and evidence_ids <= ids
                    and evidence["page"] in _pages
                ]
                self.assertTrue(supporting, f"{row['id']}[{index}]")
                # The carrying chunk is the supporting chunk whose element set
                # IS the evidence element_ids (the re-anchor always anchors to
                # one chunk's full element set); fall back to deepest overlap.
                exact = [item for item in supporting if item[0] == evidence_ids]
                if exact:
                    carrier_ids, carrier_text = exact[0]
                else:
                    carrier_ids, carrier_text = max(
                        supporting, key=lambda item: len(item[0] & evidence_ids)
                    )
                elements = [
                    by_file_elements[evidence["doc_file"]][element_id]
                    for element_id in carrier_ids
                    if element_id in by_file_elements[evidence["doc_file"]]
                ]
                policy, _quality, _reasons = chunk_usage_policy(elements, carrier_text)
                if evidence.get("usage_policy") != policy:
                    mismatches.append(
                        f"{row['id']}[{index}]:{evidence['doc_file']}:"
                        f"annotated={evidence.get('usage_policy')} carrying={policy}"
                    )
        self.assertEqual(
            mismatches,
            [],
            "usage_policy annotations must match the carrying chunk: " + "; ".join(mismatches),
        )

    def test_schema_file_matches_vocabulary(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "rag-eval-golden-case-v2")
        kinds = schema["properties"]["evidence"]["items"]["properties"]["quote_kind"]["enum"]
        self.assertEqual(set(kinds), _LOCATABLE_KINDS | {"page_excerpt"})
        pending = schema["properties"]["evidence"]["items"]["properties"]["pending"]["items"]["enum"]
        self.assertEqual(set(pending), _ALLOWED_PENDING)


if __name__ == "__main__":
    unittest.main()
