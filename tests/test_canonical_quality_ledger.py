from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from src.evaluation.canonical_quality_ledger import (
    build_quality_ledger,
    write_immutable_quality_report,
)


SHA = "a" * 64
CFG = "b" * 64
RID = "up-text00000000000000000001"


TEXT = "Drainage requirements"


def _write_pdf(path: Path, text: str = TEXT) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((30, 60), text, fontsize=12)
    doc.save(path)
    doc.close()


def _inputs(tmp_path: Path, *, route: str = "native"):
    pdf = tmp_path / "source.pdf"
    _write_pdf(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    region = {
        "region_id": RID,
        "element_id": RID,
        "file_sha256": digest,
        "file_name": "source.pdf",
        "document_version_id": "doc_test",
        "physical_page": 1,
        "kind": "text",
        "bbox": [25.0, 40.0, 180.0, 75.0],
        "execution_status": "completed",
        "quality_status": "passed" if route == "native" else "needs_review",
        "issues": [],
        "relations": [],
        "processing_config_id": "cfg-1",
        "text": TEXT,
    }
    page_doc = {
        "sha256": digest,
        "file_name": "source.pdf",
        "canonical_rel_path": "source.pdf",
        "config_hash": CFG,
        "pages": [{
            "physical_page": 1,
            "width": 300.0,
            "height": 400.0,
            "rotation": 0,
            "extraction_route": route,
            "decision_status": "approved",
            "decision_reasons": [],
            "layout_status": "completed",
            "structure_execution_complete": True,
            "content_types": {
                "text": {"execution_status": "completed"},
                "table": {"execution_status": "not_detected"},
                "formula": {"execution_status": "not_detected"},
                "image": {"execution_status": "not_detected"},
            },
            "regions": [region],
            "elements": [],
            "tables": [],
            "text": TEXT,
        }],
    }
    canonical = {
        "source": {"sha256": digest, "file_name": "source.pdf", "page_count": 1},
        "pages": [{"physical_page": 1, "width": 300.0, "height": 400.0}],
        "elements": [{
            "element_id": "e000001",
            "type": "text",
            "content": {"text": TEXT},
            "source_spans": [{
                "span_id": "s000001", "role": "primary", "physical_page": 1,
                "bbox": [25.0, 40.0, 180.0, 75.0], "orientation_degrees": 0,
            }],
            "source_region_ids": [RID],
            "processing_config_ids": ["cfg-1"],
            "quality_status": "passed" if route == "native" else "needs_review",
            "policy_reasons": [],
            "quality_issues": [],
            "provenance": [],
            "links": [],
        }],
        "structured_outcomes": [],
    }
    manifest = {
        "corpus_version": "corpus-test",
        "selected_content_count": 1,
        "page_count": 1,
        "assets": [{
            "sha256": digest,
            "canonical_rel_path": "source.pdf",
            "display_file_name": "source.pdf",
            "page_count": 1,
        }],
    }
    return manifest, [page_doc], [canonical]


def _entries(report, object_type: str):
    return [e for e in report["entries"] if e["object_type"] == object_type]


def test_native_text_closes_every_source_object_and_passes_with_pdf_evidence(tmp_path):
    report = build_quality_ledger(tmp_path, *_inputs(tmp_path))

    assert report["gate"]["status"] == "passed"
    assert report["summary"]["object_counts"] == {
        "source_document": 1,
        "source_page": 1,
        "page_content_type": 4,
        "source_region": 1,
        "canonical_element": 1,
        "source_signal": 0,
    }
    region = _entries(report, "source_region")[0]
    assert region["content_quality_conclusion"] == "verified_passed"
    assert "independent_pdf_text_layer_match" in region["evidence"]["methods"]
    element = _entries(report, "canonical_element")[0]
    assert element["content_quality_conclusion"] == "verified_passed"
    assert element["source_identity"]["region_ids"] == [RID]


def test_ocr_text_is_not_auto_promoted_to_passed(tmp_path):
    report = build_quality_ledger(tmp_path, *_inputs(tmp_path, route="full_ocr"))
    region = _entries(report, "source_region")[0]
    element = _entries(report, "canonical_element")[0]
    assert region["content_quality_conclusion"] == "manual_review_required"
    assert element["content_quality_conclusion"] == "manual_review_required"
    assert report["gate"]["status"] == "needs_adjudication"


def test_ocr_text_can_pass_only_with_strict_identity_bound_automatic_evidence(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path, route="full_ocr")
    digest = docs[0]["sha256"]
    normalised = "Drainagerequirements"
    evidence = {
        RID: {
            "region_id": RID,
            "file_sha256": digest,
            "physical_page": 1,
            "source_text_sha256": hashlib.sha256(normalised.encode("utf-8")).hexdigest(),
            "automatic_conclusion": "verified_passed",
            "strict_text_match": True,
            "verification_authority": "automatic_local_model_comparison",
            "method": "independent_rapidocr_ppocrv6_strict_region_match",
        }
    }
    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_text_verifications=evidence,
    )
    region = _entries(report, "source_region")[0]
    assert region["content_quality_conclusion"] == "verified_passed"
    assert report["gate"]["status"] == "passed"

    evidence[RID]["physical_page"] = 2
    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_text_verifications=evidence,
    )
    assert report["gate"]["status"] == "failed"
    assert any(i["code"] == "independent_text_evidence_identity_mismatch"
               for i in report["integrity_issues"])


def test_orphan_canonical_region_reference_fails_integrity_gate(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    canonicals[0]["elements"][0]["source_region_ids"] = ["up-missing"]
    report = build_quality_ledger(tmp_path, manifest, docs, canonicals)
    assert report["gate"]["status"] == "failed"
    assert any(i["code"] == "canonical_source_region_missing" for i in report["integrity_issues"])


def test_structured_region_without_element_needs_explicit_outcome(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    formula = dict(docs[0]["pages"][0]["regions"][0])
    formula.update({
        "region_id": "up-formula0000000000000001",
        "element_id": "up-formula0000000000000001",
        "kind": "formula",
        "quality_status": "needs_review",
    })
    docs[0]["pages"][0]["regions"].append(formula)
    docs[0]["pages"][0]["content_types"]["formula"] = {"execution_status": "completed"}

    report = build_quality_ledger(tmp_path, manifest, docs, canonicals)
    assert report["gate"]["status"] == "failed"
    assert any(i["code"] == "structured_region_without_disposition" for i in report["integrity_issues"])

    canonicals[0]["structured_outcomes"].append({
        "region_id": formula["region_id"],
        "kind": "formula",
        "physical_page": 1,
        "bbox": formula["bbox"],
        "execution_status": "unsupported",
        "quality_status": "needs_review",
        "issues": ["below_gate_threshold"],
        "element_id": None,
        "attempts": [],
    })
    report = build_quality_ledger(tmp_path, manifest, docs, canonicals)
    formula_entry = next(e for e in _entries(report, "source_region") if e["object_id"] == formula["region_id"])
    assert formula_entry["content_quality_conclusion"] == "unsupported"
    assert not any(i["code"] == "structured_region_without_disposition" for i in report["integrity_issues"])


def test_duplicate_source_object_id_fails_gate(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    docs[0]["pages"][0]["regions"].append(dict(docs[0]["pages"][0]["regions"][0]))
    report = build_quality_ledger(tmp_path, manifest, docs, canonicals)
    assert report["gate"]["status"] == "failed"
    assert any(i["code"] == "duplicate_source_region_id" for i in report["integrity_issues"])


def test_source_caption_exactly_preserved_in_canonical_text_is_verified(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    signal = {
        "file_sha256": docs[0]["sha256"],
        "physical_page": 1,
        "reason": "original_table_caption_candidate_not_assigned",
        "bbox": [25.0, 40.0, 180.0, 75.0],
        "source_block_index": 0,
        "original_text": TEXT,
    }

    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals, source_signals=[signal],
    )
    entry = _entries(report, "source_signal")[0]
    assert entry["content_quality_conclusion"] == "verified_passed"
    assert "source_caption_signal_exactly_preserved_in_canonical_text" in entry["evidence"]["methods"]
    assert entry["evidence"]["details"]["original_text"] == TEXT


def test_vector_inside_any_verified_region_is_accounted_for(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    signal = {
        "file_sha256": docs[0]["sha256"],
        "physical_page": 1,
        "reason": "original_vector_graphic_candidate_not_assigned",
        "bbox": [30.0, 45.0, 100.0, 70.0],
        "source_drawing_index": 0,
    }

    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals, source_signals=[signal],
    )
    entry = _entries(report, "source_signal")[0]
    assert entry["content_quality_conclusion"] == "verified_passed"
    assert "vector_signal_contained_in_verified_source_region" in entry["evidence"]["methods"]
    assert entry["evidence"]["details"]["verified_source_region_ids"] == [RID]


def test_corrupt_punctuation_text_layer_token_is_disposed_only_with_verified_ocr_projection(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path, route="full_ocr")
    signal = {
        "file_sha256": docs[0]["sha256"],
        "physical_page": 1,
        "reason": "original_text_block_not_covered_by_layout",
        "bbox": [25.0, 40.0, 180.0, 75.0],
        "source_block_index": 0,
        "original_text": "$$!",
    }
    page_evidence = {
        (docs[0]["sha256"], 1): {
            "lines": [{
                "bbox": [25.0, 40.0, 180.0, 75.0],
                "text": TEXT,
                "score": 0.99,
            }],
        },
    }

    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_page_evidence=page_evidence,
        source_signals=[signal],
    )
    entry = _entries(report, "source_signal")[0]
    assert entry["content_quality_conclusion"] == "verified_passed"
    assert entry["evidence"]["details"]["disposition"] == "corrupt_native_text_layer_token_replaced_by_verified_ocr"

    page_evidence[(docs[0]["sha256"], 1)]["lines"][0]["score"] = 0.80
    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_page_evidence=page_evidence,
        source_signals=[signal],
    )
    assert _entries(report, "source_signal")[0]["content_quality_conclusion"] == "manual_review_required"


def test_full_page_scan_is_a_carrier_when_page_has_identity_bound_independent_ocr(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path)
    signal = {
        "file_sha256": docs[0]["sha256"],
        "physical_page": 1,
        "reason": "original_full_page_image_layout_semantics_not_independently_verified",
        "bbox": [0.0, 0.0, 300.0, 400.0],
        "source_image_index": 0,
    }
    page_evidence = {
        (docs[0]["sha256"], 1): {
            "file_sha256": docs[0]["sha256"],
            "physical_page": 1,
            "lines": [{"bbox": [25.0, 40.0, 180.0, 75.0], "text": TEXT, "score": 0.99}],
        },
    }

    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_page_evidence=page_evidence,
        source_signals=[signal],
    )
    entry = _entries(report, "source_signal")[0]
    assert entry["content_quality_conclusion"] == "verified_passed"
    assert entry["evidence"]["details"]["disposition"] == "source_page_carrier_not_semantic_figure"


def test_vector_text_outline_is_accounted_by_high_confidence_ocr_preserved_in_canonical(tmp_path):
    manifest, docs, canonicals = _inputs(tmp_path, route="full_ocr")
    signal = {
        "file_sha256": docs[0]["sha256"],
        "physical_page": 1,
        "reason": "original_vector_graphic_candidate_not_assigned",
        "bbox": [30.0, 200.0, 100.0, 220.0],
        "source_drawing_index": 0,
    }
    page_evidence = {
        (docs[0]["sha256"], 1): {
            "file_sha256": docs[0]["sha256"], "physical_page": 1,
            "lines": [{"bbox": [25.0, 195.0, 180.0, 225.0], "text": TEXT, "score": 0.99}],
        },
    }

    report = build_quality_ledger(
        tmp_path, manifest, docs, canonicals,
        independent_page_evidence=page_evidence,
        source_signals=[signal],
    )
    entry = _entries(report, "source_signal")[0]
    assert entry["content_quality_conclusion"] == "verified_passed"
    assert "vector_signal_is_text_outline_preserved_by_independent_ocr" in entry["evidence"]["methods"]


def test_report_write_is_content_addressed_and_immutable(tmp_path):
    report = build_quality_ledger(tmp_path, *_inputs(tmp_path))
    out = write_immutable_quality_report(tmp_path, report)
    assert write_immutable_quality_report(tmp_path, report) == out
    summary = out / "quality_summary.json"
    summary.write_text("tampered", encoding="utf-8")
    try:
        write_immutable_quality_report(tmp_path, report)
    except FileExistsError as exc:
        assert "immutable quality artifact conflict" in str(exc)
    else:
        raise AssertionError("tampered immutable report was silently overwritten")
