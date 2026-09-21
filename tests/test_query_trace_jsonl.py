import json
import tempfile
import unittest
from pathlib import Path

from src.adapters.jsonl_query_trace_recorder import JsonlQueryTraceRecorder
from src.application.answer_generation import ModelUsage
from src.application.query_observability import QueryTrace


class JsonlQueryTraceRecorderTest(unittest.TestCase):
    def test_trace_is_appended_as_one_machine_readable_record(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "observability" / "queries.jsonl"
            recorder = JsonlQueryTraceRecorder(output_path)
            trace = QueryTrace(
                query_id="query-jsonl-001",
                question="测试问题",
                caller_type="test",
                corpus_version="formal-corpus-v1",
                status="answered",
                started_at="2026-09-07T00:00:00+00:00",
                finished_at="2026-09-07T00:00:01+00:00",
                stage_elapsed_ms={"scope": 1.0, "total": 1000.0},
                resolved_scope={"jurisdiction": "全国"},
                evidence_ids=["ev_test"],
                claim_evidence={"claim-1": ["ev_test"]},
                model_usage=ModelUsage(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                    estimated_cost=0.00014,
                    currency="CNY",
                    pricing_status="calculated",
                ),
                runtime_metadata={"provider": "test", "model": "test-model"},
            )

            recorder.record(trace)

            lines = output_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            saved = json.loads(lines[0])
            self.assertEqual(saved["query_id"], "query-jsonl-001")
            self.assertEqual(saved["model_usage"]["total_tokens"], 120)
            self.assertEqual(saved["runtime_metadata"]["model"], "test-model")
            self.assertNotIn("api_key", saved)


if __name__ == "__main__":
    unittest.main()
