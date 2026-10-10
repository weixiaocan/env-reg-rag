from __future__ import annotations

from src.ingestion.block_assembly import (
    build_page_blocks,
    heading_descriptor,
    map_model_label,
    prepare_page_document_v3,
    split_clause_lines,
)
from src.ingestion.page_furniture import classify_page_furniture, watermark_line_variants


def _line(text: str, y: float, x: float = 50.0) -> dict:
    return {"element_id": f"e-{y}-{x}", "text": text,
            "bbox": [x, y, 340.0, y + 12.0]}


def test_fixed_label_mapping() -> None:
    assert map_model_label("text") == "body"
    assert map_model_label("paragraph_title") == "title"
    assert map_model_label("number") == "furniture"
    assert map_model_label("content") == "toc"
    assert map_model_label("table") == "structured"
    assert map_model_label("reference_bibliography") == "reference"


def test_page_furniture_patterns_and_body_counterexamples() -> None:
    assert classify_page_furniture(model_label="text", text="· 7 ·",
        bbox=[180, 820, 210, 835], page_height=842).rule_id == "fallback:footer_page_number"
    assert classify_page_furniture(model_label="number", text="12",
        bbox=[180, 400, 210, 415], page_height=842).role == "page_number"
    assert classify_page_furniture(model_label="text", text="2024年排水量为12万吨。",
        bbox=[50, 300, 350, 320], page_height=842) is None
    assert classify_page_furniture(model_label="text", text="住房城乡建设部",
        bbox=[100, 400, 250, 420], page_height=842,
        watermarks=["住房城乡建设部"]).role == "watermark"
    assert classify_page_furniture(model_label="text",
        text="住房城乡建设部发布了本规范。", bbox=[50, 300, 350, 320],
        page_height=842, watermarks=["住房城乡建设部"]) is None
    assert classify_page_furniture(model_label="text", text="住房城乡建设部",
        bbox=[410, 130, 512, 150], page_height=842, page_width=560,
        watermarks=["住房城乡建设部"]) is None


def test_watermark_fragments_match_only_as_complete_lines() -> None:
    watermarks = watermark_line_variants(["信息公开", "浏览专用"])
    assert {"公开", "专用", "浏览"} <= watermarks
    assert classify_page_furniture(
        model_label="text", text="公开", bbox=[50, 300, 350, 320],
        page_height=842, watermarks=watermarks,
    ).role == "watermark"
    assert classify_page_furniture(
        model_label="text", text="本规范向社会公开。", bbox=[50, 300, 350, 320],
        page_height=842, watermarks=watermarks,
    ) is None


def test_clause_split_requires_continuity_and_keeps_numeric_continuation() -> None:
    lines = [_line("5.6.1 第一条正文。", 100), _line("续行。", 115),
             _line("5.6.2 第二条正文。", 140)]
    assert [len(group) for group in split_clause_lines(lines)] == [2, 1]
    continuation = [_line("5.6.1 第一条正文。", 100),
                    _line("5.6.2、5.6.5 和 5.7.1 条的规定", 115)]
    assert len(split_clause_lines(continuation)) == 1


def test_spaced_and_letter_o_clause_prefixes_are_normalized_for_structure() -> None:
    blocks = build_page_blocks({
        "physical_page": 1, "width": 397, "height": 575,
        "extraction_route": "native",
        "elements": [_line("1. o. 1 条文正文。", 100)],
        "regions": [{
            "region_id": "r1", "model_label": "text", "bbox": [45, 95, 350, 130],
        }],
    })
    assert blocks[0]["_v3_role"] == "clause"
    assert blocks[0]["_v3_heading"]["label"] == "1.0.1"


def test_sentence_ended_numbered_title_is_not_heading() -> None:
    blocks = build_page_blocks({
        "physical_page": 1, "width": 397, "height": 575,
        "elements": [_line("（九）持续推进专业化运行维护。", 100)],
        "regions": [{
            "region_id": "r1", "model_label": "paragraph_title",
            "bbox": [45, 95, 350, 130],
        }],
    })
    assert blocks[0]["_v3_role"] == "paragraph"
    assert blocks[0]["_v3_heading"] == {"label": "（九）", "title": "持续推进专业化运行维护", "level": 2}


def test_body_region_chinese_item_number_anchors_section_path() -> None:
    blocks = build_page_blocks({
        "physical_page": 1, "width": 560, "height": 842,
        "elements": [_line("（九）持续推进专业化运行维护。\n后续正文。", 100)],
        "regions": [{
            "region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 130],
        }],
    })
    assert blocks[0]["_v3_role"] == "paragraph"
    assert blocks[0]["_v3_heading"]["label"] == "（九）"


def test_heading_rules_exclude_text_blocks_and_keep_roman_and_prefixes() -> None:
    assert heading_descriptor("5 雨水系统", is_title=False) is None
    assert heading_descriptor("Ⅰ 适用范围", is_title=True) == {
        "label": "Ⅰ", "title": "适用范围", "level": 3}
    assert heading_descriptor("5.10.7 条文正文。", is_clause=True) == {
        "label": "5.10.7", "title": None, "level": 3}


def test_no_region_page_uses_geometric_fallback() -> None:
    page_doc = {
        "sha256": "a" * 64, "file_name": "fallback.pdf", "pages": [{
            "physical_page": 1, "page_index": 0, "width": 397, "height": 575,
            "extraction_route": "native", "decision_status": "approved",
            "elements": [_line("1.0.1 第一条。", 100), _line("续行。", 114),
                         _line("1.0.2 第二条。", 150)], "regions": [],
        }],
    }
    prepared = prepare_page_document_v3(page_doc)
    assert prepared["_v3_stats"]["fallback_pages"] == 1
    assert [block["_v3_role"] for block in prepared["pages"][0]["elements"]] == [
        "clause", "clause"]


def test_region_blocks_use_labels_and_extract_embedded_watermark() -> None:
    lines = [_line("4.4.22 严禁未经处理的污泥直接填埋。", 100),
             _line("住房城乡建设部", 116), _line("17", 550)]
    page = {
        "physical_page": 20, "width": 397, "height": 575, "elements": lines,
        "regions": [
            {"region_id": "r1", "model_label": "text", "bbox": [45, 95, 350, 135]},
            {"region_id": "r2", "model_label": "number", "bbox": [45, 545, 350, 570]},
        ],
    }
    blocks = build_page_blocks(page)
    roles = [block["_v3_role"] for block in blocks]
    assert roles == ["clause", "watermark", "page_number"]
    assert "住房城乡建设部" not in blocks[0]["text"]


def test_native_region_deduplicates_overlapping_fake_bold_text() -> None:
    page = {
        "physical_page": 1, "width": 397, "height": 575,
        "extraction_route": "native",
        "elements": [
            {**_line("1.0.1 伪粗体不应重复。", 100), "bbox": [50, 100, 260, 114]},
            {**_line("1.0.1 伪粗体不应重复。", 100.2), "bbox": [50.2, 100.2, 260.2, 114.2]},
        ],
        "regions": [{
            "region_id": "r1", "model_label": "text", "bbox": [45, 95, 350, 130],
        }],
    }

    blocks = build_page_blocks(page)

    assert len(blocks) == 1
    assert blocks[0]["text"] == "1.0.1 伪粗体不应重复。"


def test_native_region_preserves_legitimate_repeated_text_on_separate_rows() -> None:
    page = {
        "physical_page": 1, "width": 397, "height": 575,
        "extraction_route": "native",
        "elements": [_line("同意", 100), _line("同意", 140)],
        "regions": [{
            "region_id": "r1", "model_label": "text", "bbox": [45, 95, 350, 165],
        }],
    }

    blocks = build_page_blocks(page)

    assert len(blocks) == 1
    assert blocks[0]["text"] == "同意\n同意"
