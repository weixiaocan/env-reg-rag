from __future__ import annotations

from src.ingestion.block_assembly import (
    build_page_blocks,
    heading_descriptor,
    map_model_label,
    prepare_page_document_v3,
    split_clause_lines,
)
from src.ingestion.page_furniture import classify_page_furniture


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


def test_clause_split_requires_continuity_and_keeps_numeric_continuation() -> None:
    lines = [_line("5.6.1 第一条正文。", 100), _line("续行。", 115),
             _line("5.6.2 第二条正文。", 140)]
    assert [len(group) for group in split_clause_lines(lines)] == [2, 1]
    continuation = [_line("5.6.1 第一条正文。", 100),
                    _line("5.6.2、5.6.5 和 5.7.1 条的规定", 115)]
    assert len(split_clause_lines(continuation)) == 1


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
