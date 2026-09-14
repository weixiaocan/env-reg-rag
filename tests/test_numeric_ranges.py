import unittest
from pathlib import Path

from src.retrieval.numeric_ranges import NumericRangeIndex


ROOT = Path(__file__).resolve().parents[1]


class NumericRangeIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = NumericRangeIndex.from_artifacts(
            evidence_units_path=(
                ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
            ),
            corpus_manifest_path=(
                ROOT / "data" / "registry" / "formal-corpus-v1.json"
            ),
        )

    def test_formal_rainfall_table_matches_24_hour_value(self):
        matches = self.index.match(
            question="24小时降水量为20毫米时属于什么等级？",
            resolved_scope={"statistical_period": "24h"},
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].category, "中雨")
        self.assertEqual(matches[0].evidence_id, "ev_18de8c71615152558e1368e48ee73d1d")

    def test_formal_rainfall_table_matches_12_hour_value(self):
        matches = self.index.match(
            question="12小时降水量为20毫米时属于什么等级？",
            resolved_scope={"statistical_period": "12h"},
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].category, "大雨")

    def test_missing_period_does_not_guess_a_table_column(self):
        matches = self.index.match(
            question="降水量为20毫米时属于什么等级？",
            resolved_scope={},
        )

        self.assertEqual(matches, [])

    def test_interval_boundaries_are_deterministic(self):
        lower = self.index.match(
            question="12小时降水量为0.1毫米时属于什么等级？",
            resolved_scope={"statistical_period": "12h"},
        )
        upper = self.index.match(
            question="12小时降水量为140毫米时属于什么等级？",
            resolved_scope={"statistical_period": "12h"},
        )

        self.assertEqual([match.category for match in lower], ["小雨"])
        self.assertEqual([match.category for match in upper], ["特大暴雨"])


if __name__ == "__main__":
    unittest.main()
