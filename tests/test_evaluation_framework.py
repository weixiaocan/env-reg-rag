"""Contract tests for the evaluation framework freeze gate and trace schema."""

from __future__ import annotations

import json
import unittest

from src.evaluation.framework import (
    EvaluationMode,
    EvaluationRunConfig,
    FreezeGateError,
    build_case_trace,
    required_components,
    validate_run_config,
)


class EvaluationFrameworkTests(unittest.TestCase):
    def test_modes_have_explicit_adapter_dependencies(self) -> None:
        self.assertEqual(required_components(EvaluationMode.RETRIEVAL), {"retriever"})
        self.assertEqual(
            required_components(EvaluationMode.GENERATION),
            {"gold_context_generator", "judge"},
        )
        self.assertEqual(
            required_components(EvaluationMode.E2E),
            {"answer_e2e", "judge"},
        )

    def test_official_run_requires_frozen_clean_identified_inputs(self) -> None:
        config = EvaluationRunConfig(
            run_id="official-v1",
            modes=(EvaluationMode.RETRIEVAL,),
            run_kind="official",
            dataset_sha256="abc",
            corpus_version="corpus-v1",
            code_revision="deadbeef",
            worktree_clean=False,
        )

        with self.assertRaisesRegex(FreezeGateError, "worktree must be clean"):
            validate_run_config(config, available_components={"retriever"})

    def test_external_generation_requires_explicit_data_authorization(self) -> None:
        config = EvaluationRunConfig(
            run_id="official-v1",
            modes=(EvaluationMode.GENERATION,),
            run_kind="official",
            dataset_sha256="abc",
            corpus_version="corpus-v1",
            code_revision="deadbeef",
            worktree_clean=True,
            external_calls=True,
            external_data_authorized=False,
        )

        with self.assertRaisesRegex(FreezeGateError, "external data authorization"):
            validate_run_config(
                config,
                available_components={"gold_context_generator", "judge"},
            )

    def test_unknown_run_kind_is_rejected(self) -> None:
        config = EvaluationRunConfig(
            run_id="bad-kind",
            modes=(EvaluationMode.RETRIEVAL,),
            run_kind="draft",  # type: ignore[arg-type]
            dataset_sha256="abc",
            corpus_version="corpus-v1",
            code_revision=None,
            worktree_clean=False,
        )

        with self.assertRaisesRegex(FreezeGateError, "run_kind"):
            validate_run_config(config, available_components={"retriever"})


    def test_provisional_run_is_allowed_but_stays_labeled(self) -> None:
        config = EvaluationRunConfig(
            run_id="dev-snapshot",
            modes=(EvaluationMode.RETRIEVAL,),
            run_kind="provisional",
            dataset_sha256="abc",
            corpus_version="corpus-v1",
            code_revision=None,
            worktree_clean=False,
        )

        validated = validate_run_config(config, available_components={"retriever"})

        self.assertEqual(validated.run_kind, "provisional")

    def test_case_trace_has_one_serializable_shape_for_all_modes(self) -> None:
        trace = build_case_trace(
            case_id="G001",
            mode=EvaluationMode.E2E,
            question="问题",
            expected_facts=["事实"],
            gold_evidence=[{"doc_file": "a.pdf", "page": 3}],
            retrieved=[{"chunk_id": "c1", "physical_pages": [3]}],
            generation_context=[{"evidence_id": "e1"}],
            answer={"status": "answered", "text": "回答"},
            metrics={"recall_at_5": 1.0, "correctness": 1.0},
            judge={"verdict": "pass", "reason": "supported"},
            timings_ms={"retrieval": 12.0, "generation": 20.0},
            failure_attribution="ok",
        )

        payload = trace.to_dict()
        self.assertEqual(payload["schema_version"], "evaluation-case-trace/1.0")
        self.assertEqual(payload["mode"], "e2e")
        self.assertEqual(payload["generation_context"][0]["evidence_id"], "e1")
        self.assertEqual(json.loads(json.dumps(payload))["case_id"], "G001")


if __name__ == "__main__":
    unittest.main()
