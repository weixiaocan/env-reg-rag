"""Machine-readable schema checks for evaluation artifacts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from src.evaluation.framework import (
    EvaluationMode,
    EvaluationRunConfig,
    build_case_trace,
)


ROOT = Path(__file__).resolve().parents[1]


class EvaluationSchemaTests(unittest.TestCase):
    def test_run_config_matches_schema(self) -> None:
        payload = EvaluationRunConfig(
            run_id="dev-snapshot",
            modes=(EvaluationMode.RETRIEVAL,),
            run_kind="provisional",
            dataset_sha256="abc",
            corpus_version="corpus-v1",
            code_revision=None,
            worktree_clean=False,
        ).to_dict()
        schema = json.loads(
            (ROOT / "eval" / "schemas" / "evaluation_run_v1.schema.json")
            .read_text(encoding="utf-8")
        )

        jsonschema.validate(payload, schema)

    def test_case_trace_matches_schema(self) -> None:
        payload = build_case_trace(
            case_id="G001",
            mode=EvaluationMode.RETRIEVAL,
            question="问题",
            expected_facts=["事实"],
            gold_evidence=[{"doc_file": "a.pdf", "page": 1}],
            metrics={"recall_at_5": 1.0},
            timings_ms={"retrieval": 2.0},
        ).to_dict()
        schema = json.loads(
            (ROOT / "eval" / "schemas" / "evaluation_case_trace_v1.schema.json")
            .read_text(encoding="utf-8")
        )

        jsonschema.validate(payload, schema)


if __name__ == "__main__":
    unittest.main()
