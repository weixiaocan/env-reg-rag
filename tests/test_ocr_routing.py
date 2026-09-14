import unittest
import csv
from pathlib import Path

from src.ingestion.ocr_routing import select_ocr_route


ROOT = Path(__file__).resolve().parents[1]


class OcrRoutingPolicyTest(unittest.TestCase):
    def test_content_type_selects_resource_profile_and_review_requirement(self):
        cases = {
            "clauses": ("text_layout_light", None),
            "toc": ("text_layout_light", None),
            "clause_and_table": ("table_structure_heavy", None),
            "tables": ("table_structure_heavy", None),
            "formula": ("text_layout_light", "formula_structure"),
            "flowchart": ("text_layout_light", "visual_structure"),
            "clause_and_figure": ("text_layout_light", "visual_structure"),
        }

        for content_type, expected in cases.items():
            with self.subTest(content_type=content_type):
                decision = select_ocr_route(content_type)
                self.assertEqual(
                    (decision.profile, decision.mandatory_review_reason), expected
                )

    def test_versioned_route_plan_covers_every_ocr_candidate(self):
        with (ROOT / "data" / "registry" / "m2-ocr-route-plan-v1.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            plan = list(csv.DictReader(handle))
        with (ROOT / "data" / "registry" / "m2-page-sample-v0.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            samples = list(csv.DictReader(handle))

        candidates = {
            row["sample_id"]: row
            for row in samples
            if row["route_hypothesis"]
            in {"full_ocr_candidate", "hybrid_candidate"}
        }
        self.assertEqual(len(plan), 24)
        self.assertEqual({row["sample_id"] for row in plan}, set(candidates))
        self.assertEqual(
            {row["status"] for row in plan}, {"proposed_after_profile_comparison"}
        )
        for row in plan:
            decision = select_ocr_route(row["content_type"])
            self.assertEqual(row["ocr_profile"], decision.profile)
            self.assertEqual(
                row["mandatory_review_reason"],
                decision.mandatory_review_reason or "",
            )


if __name__ == "__main__":
    unittest.main()
