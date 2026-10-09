from __future__ import annotations

from types import SimpleNamespace

from src.evaluation.ocr_sampling import (
    bbox_to_pixel_rect,
    build_judgments_summary,
    chunk_exclusion_reasons,
    context_clip,
    sample_record,
    select_document_samples,
    split_chunk_fields,
    split_sampling_population,
    union_bboxes_by_page,
    validate_judgments,
    wilson_interval,
)


def _chunk(
    index: int,
    page: int,
    *,
    reasons: list[str] | None = None,
    element_types: list[str] | None = None,
    spans: list[tuple[int, list[float]]] | None = None,
    section_path: list[dict[str, str | None]] | None = None,
    text: str | None = None,
):
    return SimpleNamespace(
        chunk_id=f"chunk_{index:032x}",
        text=text or f"text {index}",
        metadata={
            "usage_policy": "source_locator_only",
            "policy_reasons": reasons or ["ocr_source_review", "needs_review_source"],
            "element_types": element_types or ["text"],
            "section_path": section_path or [],
        },
        source_spans=[
            SimpleNamespace(physical_page=p, bbox=bbox)
            for p, bbox in (spans or [(page, [1.0, 2.0, 3.0, 4.0])])
        ],
    )


def test_sampling_is_deterministic_and_covers_front_middle_back():
    chunks = [_chunk(index, index + 1) for index in range(90)]
    first = select_document_samples(chunks, "a" * 64)
    second = select_document_samples(list(reversed(chunks)), "a" * 64)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len(first) == 25
    pages = [chunk.source_spans[0].physical_page for chunk in first]
    assert any(page <= 30 for page in pages)
    assert any(31 <= page <= 60 for page in pages)
    assert any(page >= 61 for page in pages)


def test_sampling_population_accepts_only_pure_ocr_source_text():
    accepted = _chunk(1, 1, reasons=["ocr_source_review", "needs_review_source", "too_short"])
    structured = _chunk(2, 2, element_types=["text", "table"])
    quarantined = _chunk(3, 3, reasons=["ocr_source_review", "quarantined_source"])
    mojibake = _chunk(4, 4, reasons=["needs_review_source", "mojibake_symbol_text"])
    eligible, excluded = split_sampling_population(
        [accepted, structured, quarantined, mojibake]
    )
    assert eligible == [accepted]
    assert excluded["contains_table_element"] == 1
    assert excluded["quarantined_source"] == 1
    assert excluded["mojibake_symbol_text"] == 1
    assert chunk_exclusion_reasons(accepted) == []


def test_union_bboxes_by_page():
    spans = [
        SimpleNamespace(physical_page=2, bbox=[10, 20, 30, 40]),
        SimpleNamespace(physical_page=2, bbox=[5, 25, 35, 38]),
        SimpleNamespace(physical_page=3, bbox=[1, 2, 3, 4]),
    ]
    assert union_bboxes_by_page(spans) == [
        {"physical_page": 2, "bbox": [5.0, 20.0, 35.0, 40.0]},
        {"physical_page": 3, "bbox": [1.0, 2.0, 3.0, 4.0]},
    ]


def test_cross_page_record_has_one_crop_per_page():
    chunk = _chunk(
        1,
        4,
        spans=[(4, [1, 2, 3, 4]), (5, [5, 6, 7, 8]), (5, [4, 7, 9, 10])],
    )
    record = sample_record(
        chunk,
        document_name="scan.pdf",
        document_sha256="b" * 64,
        sample_id="ocr-bbbbbbbbbbbb-001",
    )
    assert record["physical_pages"] == [4, 5]
    assert [row["physical_page"] for row in record["screenshots"]] == [4, 5]
    assert record["page_bboxes"][1]["bbox"] == [4.0, 6.0, 9.0, 10.0]
    assert all(set(row) >= {"context", "page_thumbnail"} for row in record["screenshots"])


def test_population_smaller_than_25_is_all_selected_in_stable_order():
    chunks = [_chunk(3, 9), _chunk(1, 2), _chunk(2, 5)]
    selected = select_document_samples(chunks, "c" * 64)
    assert [chunk.chunk_id for chunk in selected] == [
        chunks[1].chunk_id,
        chunks[2].chunk_id,
        chunks[0].chunk_id,
    ]


def test_context_clip_uses_body_width_and_clamps_vertical_page_edges():
    page_rect = [0.0, 0.0, 600.0, 800.0]
    assert context_clip(page_rect, [10.0, 5.0, 590.0, 20.0]) == [
        24.0, 0.0, 576.0, 60.0,
    ]
    assert context_clip(page_rect, [250.0, 780.0, 300.0, 799.0]) == [
        24.0, 740.0, 576.0, 800.0,
    ]


def test_thumbnail_bbox_coordinate_conversion_scales_and_clamps():
    assert bbox_to_pixel_rect(
        [0.0, 0.0, 600.0, 800.0],
        [-5.0, 72.0, 610.0, 144.0],
        dpi=100,
    ) == [0, 100, 834, 200]


def test_chunk_fields_are_split_from_structural_section_path():
    section_path = [
        {"label": "3", "title": "一般规定"},
        {"label": "3.2.3", "title": None},
    ]
    prefix = "3 一般规定 > 3.2.3\n"
    chunk = _chunk(1, 1, section_path=section_path, text=prefix + "内涝防治正文。")
    assert split_chunk_fields(chunk) == {
        "heading_path_text": "3 一般规定 > 3.2.3",
        "body_text": "内涝防治正文。",
        "heading_prefix_in_chunk": True,
    }


def test_chunk_fields_keep_body_when_structural_prefix_was_cap_dropped():
    chunk = _chunk(
        1,
        1,
        section_path=[{"label": "9", "title": "管道功能性试验"}],
        text="独立正文。",
    )
    assert split_chunk_fields(chunk) == {
        "heading_path_text": "9 管道功能性试验",
        "body_text": "独立正文。",
        "heading_prefix_in_chunk": False,
    }


def _write_review_fixture(tmp_path):
    context = "eval/audits/ocr_sampling/crops/a/context.png"
    sample = {
        "sample_id": "ocr-a-001", "document_name": "scan.pdf",
        "screenshots": [{"context": {"path": context}}],
    }
    judgment = {
        "sample_id": "ocr-a-001", "judge": "codex", "model": "test-model",
        "text_fidelity": "error", "error_types": ["char"],
        "heading_path": "wrong", "standalone_usable": "fragment",
        "evidence": "OCR『甲』 vs 原图『乙』", "unjudgeable_reason": None,
        "images_viewed": [context], "notes": "",
    }
    sheet = tmp_path / "sheet.jsonl"
    judgments = tmp_path / "judgments.jsonl"
    import json
    sheet.write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    judgments.write_text(json.dumps(judgment, ensure_ascii=False) + "\n", encoding="utf-8")
    return sheet, judgments


def test_validate_judgments_requires_all_context_images(tmp_path):
    sheet, judgments = _write_review_fixture(tmp_path)
    assert validate_judgments(sheet, judgments) == {"sample_count": 1, "judgment_count": 1}
    text = judgments.read_text(encoding="utf-8").replace(
        '"images_viewed": ["eval/audits/ocr_sampling/crops/a/context.png"]',
        '"images_viewed": []',
    )
    judgments.write_text(text, encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="context images not viewed"):
        validate_judgments(sheet, judgments)


def test_validate_judgments_rejects_error_without_evidence(tmp_path):
    sheet, judgments = _write_review_fixture(tmp_path)
    text = judgments.read_text(encoding="utf-8").replace(
        '"evidence": "OCR\\u300e\\u7532\\u300f vs \\u539f\\u56fe\\u300e\\u4e59\\u300f"',
        '"evidence": ""',
    )
    # ensure_ascii=False leaves the Chinese text literal.
    text = text.replace('"evidence": "OCR『甲』 vs 原图『乙』"', '"evidence": ""')
    judgments.write_text(text, encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="requires evidence"):
        validate_judgments(sheet, judgments)


def test_judgment_summary_reports_wilson_and_independent_rates(tmp_path):
    sheet, judgments = _write_review_fixture(tmp_path)
    summary = build_judgments_summary(sheet, judgments)
    row = summary["documents"][0]
    assert row["error_rate"] == 1.0
    assert row["heading_wrong_rate"] == 1.0
    assert row["fragment_rate"] == 1.0
    assert row["error_rate_wilson_95"] == wilson_interval(1, 1)
