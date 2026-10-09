import json

import pytest

from src.application.canonical_assembly import assemble_document
from src.application.canonical_validation import apply_computed_ids, compute_ids, validate_document
from src.ingestion.independent_ocr_fusion import (
    align_page,
    classify_noise,
    fuse_document_pages,
    fuse_page_text,
)


def _element(text, bbox, element_id="p0000-ocr-0001"):
    return {"element_id": element_id, "type": "text", "text": text,
            "normalized_text": text, "bbox": bbox, "reading_order": 0}


def _line(text, bbox, line_id="l0001", score=0.999):
    return {"line_id": line_id, "text": text, "bbox": bbox, "score": score}


def _text_region(bbox=(0, 0, 200, 200)):
    return {"region_id": "r-text", "kind": "text", "bbox": list(bbox),
            "quality_status": "needs_review", "issues": []}


def test_align_page_classifies_same_differ_pp_only_and_primary_only():
    primary = [
        _element("相同", [10, 10, 50, 20], "a"),
        _element("主文本", [10, 30, 50, 40], "b"),
        _element("仅主", [10, 50, 50, 60], "c"),
    ]
    pp = [
        _line("相同", [10, 10, 50, 20], "p1"),
        _line("独立文本", [10, 30, 50, 40], "p2"),
        _line("仅独立", [10, 70, 50, 80], "p3"),
    ]
    rows = align_page(primary, pp, [_text_region()])
    assert {r["classification"] for r in rows} == {
        "same", "differ", "pp_only", "primary_only"}


def test_noise_filter_records_specific_reasons():
    assert classify_noise("住房城乡建设部", 0.999) == "known_watermark_text"
    assert classify_noise("字", 0.999) == "single_character"
    assert classify_noise("有效正文", 0.94) == "score_below_0.95"
    assert classify_noise("有效正文", 0.99) is None


def test_fusion_inserts_in_y_order_and_records_complete_audit():
    page = {
        "page_index": 0, "physical_page": 1, "extraction_route": "full_ocr",
        "elements": [_element("第一行", [10, 10, 80, 20])],
        "regions": [_text_region()],
    }
    independent = {"lines": [
        _line("第一行", [10, 10, 80, 20], "l1"),
        _line("补入正文", [10, 30, 90, 40], "l2", 0.999),
    ]}
    fused, counts = fuse_page_text(
        page, independent, run_id="run-1", run_manifest_sha256="a" * 64)
    assert counts == {"inserted": 1, "replaced": 0}
    assert [e["text"] for e in fused["elements"]] == ["第一行", "补入正文"]
    audit = fused["elements"][1]["_ocr_fusion"]
    assert audit == {
        "action": "insert", "engine": "RapidOCR/PP-OCRv6_rec_small",
        "run_id": "run-1", "run_manifest_sha256": "a" * 64,
        "rule_version": "t5-calibrated-v1", "original_primary_text": None,
        "independent_text": "补入正文", "independent_line_id": "l2",
        "independent_score": 0.999, "alignment_score": None,
        "source_region_ids": ["r-text"],
    }


def test_fusion_does_not_touch_structured_or_low_confidence_pp_only():
    page = {
        "page_index": 0, "physical_page": 1, "extraction_route": "full_ocr",
        "elements": [],
        "regions": [
            _text_region((0, 0, 200, 40)),
            {"region_id": "r-table", "kind": "table", "bbox": [0, 50, 200, 100]},
        ],
    }
    independent = {"lines": [
        _line("低置信正文", [10, 10, 80, 20], "low", 0.96),
        _line("表格文字", [10, 60, 80, 70], "table", 0.999),
    ]}
    fused, counts = fuse_page_text(
        page, independent, run_id="run-1", run_manifest_sha256="a" * 64)
    assert fused["elements"] == []
    assert counts == {"inserted": 0, "replaced": 0}


def test_document_fusion_hard_fails_when_run_or_page_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="run manifest not found"):
        fuse_document_pages({"sha256": "a" * 64, "pages": []},
                            run_dir=str(tmp_path / "missing"))

    run = tmp_path / "run-1"
    run.mkdir()
    (run / "run_manifest.json").write_text(json.dumps({
        "schema_version": "independent-ocr-run-v1", "run_id": "run-1"}),
        encoding="utf-8")
    doc = {"sha256": "a" * 64, "pages": [{
        "physical_page": 1, "width": 100, "height": 100,
        "extraction_route": "full_ocr", "elements": [], "regions": [],
    }]}
    with pytest.raises(FileNotFoundError, match="independent OCR page not found"):
        fuse_document_pages(doc, run_dir=str(run))


def test_canonical_text_carries_fusion_provenance_and_stays_needs_review(tmp_path):
    sha = "b" * 64
    run = tmp_path / "run-1"
    page_dir = run / "pages" / sha
    page_dir.mkdir(parents=True)
    (run / "run_manifest.json").write_text(json.dumps({
        "schema_version": "independent-ocr-run-v1", "run_id": "run-1"}),
        encoding="utf-8")
    (page_dir / "p0001.json").write_text(json.dumps({
        "file_sha256": sha, "physical_page": 1,
        "render": {"width_px": 400, "height_px": 600, "scale": 2.0},
        "lines": [
            _line("已有正文", [10, 10, 80, 20], "l1"),
            _line("补入正文", [10, 30, 90, 40], "l2", 0.999),
        ],
    }, ensure_ascii=False), encoding="utf-8")
    page_doc = {
        "sha256": sha, "file_name": "synthetic.pdf", "pages": [{
            "physical_page": 1, "page_index": 0, "width": 200.0, "height": 300.0,
            "rotation": 0, "extraction_route": "full_ocr",
            "parser_name": "paddleocr-text", "engine_version": "3.7.0",
            "decision_status": "approved", "decision_reasons": [],
            "ocr_confidence": 0.99,
            "elements": [_element("已有正文", [10, 10, 80, 20])],
            "regions": [_text_region()],
        }],
    }
    doc = assemble_document(
        page_doc, formula_pages={}, independent_ocr_run_dir=str(run))
    inserted = next(
        e for e in doc["elements"]
        if e["content"]["text"] == "已有正文\n补入正文"
    )
    assert [span["role"] for span in inserted["source_spans"]] == [
        "primary", "continuation",
    ]
    assert inserted["quality_status"] == "needs_review"
    assert inserted["policy_reasons"][-1] == "independent_ocr_fusion_unreviewed"
    assert inserted["provenance"][-1] == {
        "operation": "merge", "method": "ocr", "engine_id": "ocr-fusion-1",
        "confidence": 0.999,
    }
    assert inserted["ocr_fusion"][0]["run_id"] == "run-1"
    assert any(e["engine_id"] == "ocr-fusion-1" for e in doc["generation"]["engines"])
    apply_computed_ids(doc, compute_ids(doc))
    validate_document(doc)
