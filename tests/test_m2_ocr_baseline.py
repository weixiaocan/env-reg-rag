import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.ocr_baseline import run_ocr_baseline


ROOT = Path(__file__).resolve().parents[1]


class FixtureOcrEngine:
    name = "fixture-structure-engine"
    version = "test"

    def predict(self, image):
        height, width = image.shape[:2]
        return {
            "width": width,
            "height": height,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "降水量等级",
                    "block_bbox": [0, 0, width, height],
                    "block_id": 0,
                    "block_order": 0,
                }
            ],
            "overall_ocr_res": {
                "rec_texts": ["降水量等级"],
                "rec_scores": [0.98],
                "rec_boxes": [[0, 0, width, height]],
            },
            "table_res_list": [],
            "model_settings": {"use_table_recognition": True},
        }


class InterruptingOcrEngine(FixtureOcrEngine):
    def __init__(self, interrupt_on_call=None):
        self.interrupt_on_call = interrupt_on_call
        self.call_count = 0

    def predict(self, image):
        self.call_count += 1
        if self.call_count == self.interrupt_on_call:
            raise KeyboardInterrupt("simulated hard interruption")
        return super().predict(image)


class M2OcrBaselineContractTest(unittest.TestCase):
    def test_baseline_runs_only_degradation_candidates_and_is_traceable(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_ocr_baseline(
                ROOT,
                Path(directory),
                engine=FixtureOcrEngine(),
                sample_ids={"M2-P001", "M2-P019", "M2-P028"},
                artifact_prefix="ocr-contract",
            )

            self.assertEqual(result["summary"]["eligible_sample_count"], 1)
            self.assertEqual(result["summary"]["page_count"], 1)
            self.assertEqual(result["summary"]["failed_page_count"], 0)
            self.assertEqual(result["pages"][0]["sample_id"], "M2-P001")
            self.assertEqual(result["pages"][0]["extraction_route"], "full_ocr")
            self.assertEqual(
                result["pages"][0]["table_assertion_evaluation"]["anchor_id"],
                "TAB-01",
            )
            self.assertGreater(
                result["pages"][0]["resource_usage"]["peak_working_set_mb"], 0
            )
            self.assertGreater(result["pages"][0]["resource_usage"]["rss_mb"], 0)
            self.assertEqual(result["metadata"]["parser_name"], "paddleocr-ppstructurev3")
            self.assertEqual(
                result["metadata"]["included_route_hypotheses"],
                ["full_ocr_candidate", "hybrid_candidate"],
            )

            saved = [
                json.loads(line)
                for line in (Path(directory) / "ocr-contract-pages.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual([page["sample_id"] for page in saved], ["M2-P001"])
            self.assertTrue((Path(directory) / "ocr-contract-run-metadata.json").exists())
            self.assertTrue((Path(directory) / "ocr-contract-summary.json").exists())

    def test_light_path_records_that_table_recognition_was_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_ocr_baseline(
                ROOT,
                Path(directory),
                engine=FixtureOcrEngine(),
                sample_ids={"M2-P001", "M2-P035"},
                ocr_profile="text_layout_light",
                table_recognition=False,
                artifact_prefix="ocr-light-contract",
            )

            self.assertEqual(result["summary"]["eligible_sample_count"], 1)
            self.assertEqual(result["pages"][0]["sample_id"], "M2-P035")
            self.assertFalse(result["metadata"]["config"]["table_recognition"])
            self.assertEqual(result["metadata"]["ocr_profile"], "text_layout_light")
            self.assertTrue(result["metadata"]["route_plan_sha256"])

    def test_interrupted_batch_resumes_after_last_checkpointed_page(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            interrupted_engine = InterruptingOcrEngine(interrupt_on_call=2)

            with self.assertRaises(KeyboardInterrupt):
                run_ocr_baseline(
                    ROOT,
                    output_dir,
                    engine=interrupted_engine,
                    sample_ids={"M2-P003", "M2-P035"},
                    ocr_profile="text_layout_light",
                    artifact_prefix="ocr-resume-contract",
                )

            resumed_engine = InterruptingOcrEngine()
            result = run_ocr_baseline(
                ROOT,
                output_dir,
                engine=resumed_engine,
                sample_ids={"M2-P003", "M2-P035"},
                ocr_profile="text_layout_light",
                artifact_prefix="ocr-resume-contract",
                resume=True,
            )

            self.assertEqual(resumed_engine.call_count, 1)
            self.assertEqual(result["summary"]["page_count"], 2)
            self.assertEqual(
                {page["sample_id"] for page in result["pages"]},
                {"M2-P003", "M2-P035"},
            )
            checkpoint_records = [
                json.loads(line)
                for line in (
                    output_dir / "ocr-resume-contract-checkpoint.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["record_type"] for record in checkpoint_records],
                ["run_header", "page", "page"],
            )

    def test_batch_reports_durable_progress_after_each_page(self):
        with tempfile.TemporaryDirectory() as directory:
            progress_events = []

            run_ocr_baseline(
                ROOT,
                Path(directory),
                engine=FixtureOcrEngine(),
                sample_ids={"M2-P003", "M2-P035"},
                ocr_profile="text_layout_light",
                artifact_prefix="ocr-progress-contract",
                progress_callback=progress_events.append,
            )

            self.assertEqual(
                progress_events,
                [
                    {
                        "sample_id": "M2-P003",
                        "outcome": "page",
                        "completed": 1,
                        "total": 2,
                    },
                    {
                        "sample_id": "M2-P035",
                        "outcome": "page",
                        "completed": 2,
                        "total": 2,
                    },
                ],
            )


if __name__ == "__main__":
    unittest.main()
