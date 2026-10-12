from __future__ import annotations

import json

import pytest

from src.application.ocr_admission import (
    apply_ocr_document_admission,
    load_admission_registry,
)

SHA = "a" * 64


def _registry(tmp_path, verdict="admit", **overrides):
    record = {"sha256": SHA, "verdict": verdict, "reviewer": "r",
              "samples": [{"chunk_id": "c", "pages": [1]}], **overrides}
    path = tmp_path / "admission.json"
    path.write_text(json.dumps({"admission_id": "adm-v1", "documents": [record]}),
                    encoding="utf-8")
    return load_admission_registry(path)


def _doc(*elements):
    return {"source": {"sha256": SHA}, "elements": list(elements)}


def _text(reasons, status="needs_review", kind="text"):
    return {"type": kind, "quality_status": status, "policy_reasons": list(reasons),
            "quality_issues": []}


def test_admitted_document_upgrades_only_ocr_source_blocked_text(tmp_path) -> None:
    ocr = _text(["ocr_source_review", "preprocessed_independent_ocr_unreviewed"])
    scanned = _text(["quarantined_source", "scanned_with_text_layer_requires_ocr_vote"],
                    status="quarantined")
    toc = _text(["ocr_source_review", "toc_navigation_only"])
    short = _text(["ocr_source_review", "too_short"])
    table = _text(["ocr_source_review"], kind="table")
    doc = _doc(ocr, scanned, toc, short, table)
    assert apply_ocr_document_admission(doc, _registry(tmp_path)) == 2
    assert ocr["quality_status"] == scanned["quality_status"] == "passed"
    assert ocr["quality_issues"] == [f"review_assertion:adm-v1:{SHA[:12]}"]
    assert "ocr_document_admitted" in ocr["policy_reasons"]
    assert [toc["quality_status"], short["quality_status"], table["quality_status"]] == [
        "needs_review"] * 3


def test_locator_only_verdict_and_missing_registry_change_nothing(tmp_path) -> None:
    element = _text(["ocr_source_review"])
    assert apply_ocr_document_admission(_doc(element), _registry(tmp_path, "locator_only")) == 0
    assert apply_ocr_document_admission(_doc(element), None) == 0
    assert element["quality_status"] == "needs_review"


def test_registry_requires_review_evidence(tmp_path) -> None:
    with pytest.raises(ValueError):
        _registry(tmp_path, samples=[])
    with pytest.raises(ValueError):
        _registry(tmp_path, verdict="maybe")


def _table(region, reasons=("structured_default_locator_only",)):
    return {"type": "table", "quality_status": "needs_review", "policy_reasons": list(reasons),
            "quality_issues": [], "source_region_ids": [region]}


def test_checked_table_answers_and_unchecked_table_stays_locator(tmp_path) -> None:
    from src.application.evidence_policy import ANSWER, LOCATOR, element_usage_policy

    path = tmp_path / "admission.json"
    path.write_text(json.dumps({
        "admission_id": "adm-v1",
        "documents": [{"sha256": "b" * 64, "verdict": "locator_only", "reviewer": "r",
                       "samples": [{"chunk_id": "c", "pages": [1]}]}],
        "table_admissions": [{"sha256": SHA, "region_id": "up-1",
                              "method": "text_layer_auto_check", "evidence": {"prec": 1.0}}],
    }), encoding="utf-8")
    registry = load_admission_registry(path)
    checked, unchecked = _table("up-1"), _table("up-2")
    quarantined = _table("up-1", ("structured_default_locator_only", "region_quality_quarantined"))
    assert apply_ocr_document_admission(_doc(checked, unchecked), registry) == 1
    assert apply_ocr_document_admission(_doc(quarantined), registry) == 0
    assert element_usage_policy(checked) == ANSWER
    assert element_usage_policy(unchecked) == LOCATOR
    assert element_usage_policy(quarantined) == LOCATOR


def test_table_admission_requires_method_and_evidence(tmp_path) -> None:
    path = tmp_path / "admission.json"
    record = {"sha256": SHA, "verdict": "admit", "reviewer": "r",
              "samples": [{"chunk_id": "c", "pages": [1]}]}
    path.write_text(json.dumps({"admission_id": "a", "documents": [record], "table_admissions": [
        {"sha256": SHA, "region_id": "up-1", "method": "eyeballed", "evidence": {"x": 1}}]}),
        encoding="utf-8")
    with pytest.raises(ValueError):
        load_admission_registry(path)


def test_scanned_page_region_quarantine_is_admitted_only_with_its_page_reason(tmp_path) -> None:
    scanned = _text(["quarantined_source", "region_quality_quarantined",
                     "scanned_with_text_layer_requires_ocr_vote",
                     "preprocessed_independent_ocr_unreviewed"])
    low_confidence = _text(["quarantined_source", "region_quality_quarantined",
                            "low_confidence_text"])
    assert apply_ocr_document_admission(_doc(scanned, low_confidence), _registry(tmp_path)) == 1
    assert low_confidence["quality_status"] == "needs_review"
