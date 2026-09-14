import unittest

from src.evaluation.answer_benchmark import aggregate_runs, evaluate_answer_case


class AnswerBenchmarkTest(unittest.TestCase):
    def test_answer_case_requires_expected_keyword_and_accepted_citation(self):
        case = {
            "expected_status": "answered",
            "expected_keywords": ["大雨"],
            "accepted_evidence_ids": ["ev-rain"],
        }
        result = {
            "status": "answered",
            "answer": {
                "claims": [
                    {
                        "text": "12小时20毫米属于大雨。",
                        "evidence_ids": ["ev-rain"],
                    }
                ]
            },
        }

        self.assertEqual(evaluate_answer_case(case, result), (True, []))

    def test_aggregate_reports_nearest_rank_latency_stability_and_tokens(self):
        runs = [
            {
                "round": 1,
                "case_id": "A",
                "passed": True,
                "elapsed_ms": 100,
                "error_type": None,
                "trace": {
                    "model_usage": {"total_tokens": 10, "estimated_cost": None},
                    "stage_elapsed_ms": {"retrieval": 5, "generation": 80},
                },
            },
            {
                "round": 2,
                "case_id": "A",
                "passed": True,
                "elapsed_ms": 300,
                "error_type": None,
                "trace": {
                    "model_usage": {"total_tokens": 20, "estimated_cost": None},
                    "stage_elapsed_ms": {"retrieval": 7, "generation": 250},
                },
            },
            {
                "round": 1,
                "case_id": "B",
                "passed": True,
                "elapsed_ms": 200,
                "error_type": None,
                "trace": {"model_usage": {"total_tokens": 0, "estimated_cost": None}},
            },
            {
                "round": 2,
                "case_id": "B",
                "passed": False,
                "elapsed_ms": 400,
                "error_type": "TimeoutError",
                "trace": None,
            },
        ]

        summary = aggregate_runs(runs, expected_rounds=2, expected_case_ids=["A", "B"])

        self.assertEqual(summary["request_latency_ms"]["p50"], 200)
        self.assertEqual(summary["request_latency_ms"]["p95"], 400)
        self.assertEqual(summary["stable_cases"], 1)
        self.assertEqual(summary["stable_case_rate"], 0.5)
        self.assertEqual(summary["model_calls"], 2)
        self.assertEqual(summary["total_tokens"], 30)
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["generation_latency_ms"]["p95"], 250)
        self.assertEqual(summary["retrieval_latency_ms"]["sample_count"], 2)
