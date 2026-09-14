import csv
import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "registry"


class M2PageSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (REGISTRY / "m2-page-sample-v0.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            cls.rows = list(csv.DictReader(handle))
        cls.manifest = json.loads(
            (REGISTRY / "experiment-sample-v0.json").read_text(encoding="utf-8-sig")
        )
        with (REGISTRY / "inventory.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            cls.inventory = {row["file_name"]: row for row in csv.DictReader(handle)}
        with (REGISTRY / "m2-page-probe.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            cls.probe = list(csv.DictReader(handle))
        cls.probe_metadata = json.loads(
            (REGISTRY / "m2-page-probe.meta.json").read_text(encoding="utf-8")
        )

    def test_fixed_sample_has_40_unique_pages_across_all_approved_documents(self):
        approved = {item["file_name"] for item in self.manifest["documents"]}
        selected = {row["file_name"] for row in self.rows}
        page_keys = {(row["file_name"], row["pdf_page"]) for row in self.rows}

        self.assertEqual(40, len(self.rows))
        self.assertEqual(40, len(page_keys))
        self.assertEqual(approved, selected)
        self.assertEqual(40, len({row["sample_id"] for row in self.rows}))

    def test_every_selected_page_exists_in_its_registered_pdf(self):
        for row in self.rows:
            page_number = int(row["pdf_page"])
            page_count = int(self.inventory[row["file_name"]]["pages"])
            self.assertGreaterEqual(page_number, 1, row["sample_id"])
            self.assertLessEqual(page_number, page_count, row["sample_id"])

    def test_sample_preserves_the_approved_challenge_distribution(self):
        routes = Counter(row["route_hypothesis"] for row in self.rows)

        self.assertEqual(14, routes["native_candidate"])
        self.assertEqual(7, routes["hybrid_candidate"])
        self.assertEqual(17, routes["full_ocr_candidate"])
        self.assertEqual(2, routes["blank_skip_candidate"])
        self.assertEqual(2, sum(row["content_type"] == "blank" for row in self.rows))

    def test_only_visual_blank_pages_skip_human_anchor_review(self):
        without_anchor = [
            row for row in self.rows if row["eligible_for_human_anchor"] == "no"
        ]

        self.assertEqual(2, len(without_anchor))
        self.assertTrue(all(row["content_type"] == "blank" for row in without_anchor))

    def test_probe_covers_every_registered_physical_page_without_failures(self):
        expected_pages = sum(
            int(self.inventory[item["file_name"]]["pages"])
            for item in self.manifest["documents"]
        )
        self.assertEqual(235, expected_pages)
        self.assertEqual(expected_pages, len(self.probe))
        self.assertTrue(all(row["probe_status"] == "ok" for row in self.probe))
        for item in self.manifest["documents"]:
            page_count = int(self.inventory[item["file_name"]]["pages"])
            actual = sorted(
                int(row["page_number"])
                for row in self.probe
                if row["file_name"] == item["file_name"]
            )
            self.assertEqual(list(range(1, page_count + 1)), actual)

    def test_selected_text_layer_signals_match_the_probe(self):
        probe_by_page = {
            (row["file_name"], row["page_number"]): row for row in self.probe
        }
        for selected in self.rows:
            probe = probe_by_page[(selected["file_name"], selected["pdf_page"])]
            chars = int(probe["non_whitespace_chars"])
            if selected["text_layer_signal"] == "absent":
                self.assertEqual(0, chars, selected["sample_id"])
            else:
                self.assertGreater(chars, 0, selected["sample_id"])

    def test_probe_metadata_identifies_current_inputs_and_generator(self):
        def sha256(path):
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()

        metadata = self.probe_metadata
        self.assertEqual("experiment-sample-v0", metadata["manifest_id"])
        self.assertEqual(235, metadata["page_count"])
        self.assertEqual(0, metadata["failed_page_count"])
        self.assertEqual(
            sha256(REGISTRY / "experiment-sample-v0.json"),
            metadata["manifest_sha256"],
        )
        self.assertEqual(
            sha256(ROOT / "scripts" / "m2_page_probe.py"),
            metadata["script_sha256"],
        )
        expected_hashes = {
            item["file_name"]: item["sha256"] for item in self.manifest["documents"]
        }
        self.assertEqual(
            expected_hashes,
            {item["file_name"]: item["sha256"] for item in metadata["inputs"]},
        )


if __name__ == "__main__":
    unittest.main()
