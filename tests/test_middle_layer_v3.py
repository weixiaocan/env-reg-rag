from __future__ import annotations

import pytest

from src.ingestion.block_assembly import (
    build_page_blocks,
    heading_descriptor,
    map_model_label,
    prepare_page_document_v3,
    split_clause_lines,
)
from src.application.canonical_assembly import (
    _PageItem,
    _associate_adjacent_formula_numbers,
)
from src.ingestion.ocr_region_text import (
    find_adjacent_duplicate_fragments,
    recover_source_supported_occlusion_fragments,
    remove_introduced_adjacent_duplicate_fragments,
)
from src.ingestion.page_furniture import classify_page_furniture, watermark_line_variants
from eval.audits.middle_layer_v3.run_rollout import _read_r2b_baseline_snapshot


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


def test_explicit_ocr_region_assignment_keeps_boundary_spanning_line() -> None:
    page = {
        "physical_page": 63, "width": 397, "height": 575,
        "extraction_route": "scanned_with_text_layer",
        "_v3_ocr_region_text": True,
        "elements": [{
            **_line("对雨水接入污水管道进行判定。", 254.5),
            "bbox": [41.0, 254.5, 336.0, 283.0],
            "_v3_assigned_region_id": "r-next",
        }],
        "regions": [{
            "region_id": "r-next", "model_label": "text",
            "bbox": [40.0, 270.8, 337.0, 329.5],
        }],
    }

    blocks = build_page_blocks(page)

    assert [block["text"] for block in blocks] == ["对雨水接入污水管道进行判定。"]


def test_native_region_fallback_does_not_duplicate_element_backed_clause() -> None:
    text = "5.1.1 第一条正文。\n5.1.2 第二条正文。"
    page = {
        "physical_page": 8, "width": 560, "height": 842,
        "extraction_route": "native",
        "elements": [{**_line(text, 220), "bbox": [55, 220, 525, 293]}],
        "regions": [
            {"region_id": "r-full", "model_label": "text", "bbox": [53, 218, 524, 261], "text": text},
            {"region_id": "r-echo", "model_label": "text", "bbox": [53, 264, 524, 293], "text": text},
        ],
    }
    blocks = build_page_blocks(page)
    assert [block["text"] for block in blocks] == ["5.1.1 第一条正文。", "5.1.2 第二条正文。"]


def test_single_region_keeps_normal_native_grouping() -> None:
    page = {
        "physical_page": 8, "width": 560, "height": 842,
        "elements": [_line("正文第一行。", 220), _line("正文第二行。", 238)],
        "regions": [
            {"region_id": "r-body", "model_label": "text",
             "bbox": [45, 215, 530, 260]},
        ],
    }
    assert [block["text"] for block in build_page_blocks(page)] == [
        "正文第一行。\n正文第二行。"]


def test_embedded_clause_split_keeps_continuation_lines_with_their_clause() -> None:
    page = {
        "physical_page": 8, "width": 560, "height": 842,
        "elements": [{**_line(
            "5.1.1 第一条正文。\n第一条续行。\n5.1.2 第二条正文。\n第二条续行。", 220),
            "bbox": [55, 220, 525, 293]}],
        "regions": [
            {"region_id": "r", "model_label": "text", "bbox": [53, 218, 530, 300]},
        ],
    }
    assert [block["text"] for block in build_page_blocks(page)] == [
        "5.1.1 第一条正文。\n第一条续行。", "5.1.2 第二条正文。\n第二条续行。"]


def test_native_overlapping_suffix_echo_is_removed_but_separate_row_is_kept() -> None:
    page = {
        "physical_page": 5, "width": 560, "height": 842,
        "elements": [
            {**_line("本文件按照规则的规定起草。", 168), "bbox": [71, 168, 532, 193]},
            {**_line("定起草。", 184), "bbox": [71, 184, 113, 207]},
            {**_line("定起草。", 230), "bbox": [71, 230, 113, 253]},
        ],
        "regions": [{"region_id": "r", "model_label": "text", "bbox": [60, 160, 540, 260]}],
    }
    blocks = build_page_blocks(page)
    assert blocks[0]["text"] == "本文件按照规则的规定起草。\n定起草。"


def test_vote_repairs_only_repeat_absent_from_each_source() -> None:
    sources = {"primary": "在系统之前，通过渗透", "pp_raw": "在系统之前，通过渗透", "pp_clean": "在系统之前，通过渗透"}
    merged = "在系统之前，通之前，通过渗透"
    defects = find_adjacent_duplicate_fragments(merged, sources)
    assert defects
    assert remove_introduced_adjacent_duplicate_fragments(merged, sources) == "在系统之前，通过渗透"
    legal_sources = {"primary": "氨氮、总氮、总磷", "pp_raw": None, "pp_clean": None}
    assert remove_introduced_adjacent_duplicate_fragments("氨氮、总氮、总磷", legal_sources) == "氨氮、总氮、总磷"


def test_vote_recovers_only_source_supported_occluded_fragments() -> None:
    text, repairs = recover_source_supported_occlusion_fragments(
        "下渗时间(s)。",
        {"primary": "下渗时间（s）。", "pp_clean": "下渗时间(s)。",
         "pp_raw": "tx——下渗时间(s)。"},
    )
    assert text == "tx——下渗时间(s)。"
    assert repairs[0]["kind"] == "variable_prefix"

    text, repairs = recover_source_supported_occlusion_fragments(
        "衰减常数(h-1)，可取2~7；",
        {"primary": "衰减常数(h-1)，可取2~7；", "pp_clean": None,
         "pp_raw": "k0——衰减常数(h-1)，可取2~7；"},
    )
    assert text == "k0——衰减常数(h-1)，可取2~7；"
    assert repairs[0]["restored"] == "k0——"

    text, repairs = recover_source_supported_occlusion_fragments(
        "降雨分为小雨、中雨、大雨、暴雨、大暴雨、特大暴雨、微量降雨共4个等级。",
        {"primary": "降雨分为小雨、中雨、大雨、暴雨、大暴雨、特大暴雨、微量降雨共个等级。",
         "pp_clean": None,
         "pp_raw": "降雨分为小雨、中雨、大雨、暴雨、大暴雨、特大暴雨、微量降雨共7个等级。"},
    )
    assert "共7个等级" in text
    assert repairs[0]["kind"] == "enumerated_count"

    unchanged, repairs = recover_source_supported_occlusion_fragments(
        "共个等级", {"primary": "共个等级", "pp_clean": None, "pp_raw": None})
    assert unchanged == "共个等级"
    assert repairs == []


@pytest.mark.parametrize("variable,definition", [
    ("z1", "糊率系数(m-1)，按试验值确定；"),
    ("beta", "衰减参数(h-1)，可取0.2～0.8。"),
])
def test_region_text_restores_any_evidenced_variable_prefix_hidden_by_formula_region(
    variable: str, definition: str,
) -> None:
    page = {
        "physical_page": 15, "width": 400, "height": 600,
        "regions": [{
            "region_id": "text-definition", "kind": "text", "model_label": "text",
            "bbox": [70, 480, 240, 500],
            "text": f"{variable}\n——{definition}",
        }],
        "elements": [{
            "element_id": "merged-suffix", "type": "text",
            "text": definition,
            "bbox": [110, 488, 235, 498],
            "_v3_assigned_region_id": "text-definition",
        }],
    }
    blocks = build_page_blocks(page)
    assert blocks[0]["text"] == f"{variable}——{definition}"
    assert blocks[0]["_v3_region_text_fallback"] is True

    page["elements"] = []
    page["_v3_ocr_region_text"] = True
    blocks = build_page_blocks(page)
    assert blocks[0]["text"] == f"{variable}——{definition}"


def test_toc_leader_page_is_navigation_only_but_prose_ellipsis_is_body() -> None:
    toc = build_page_blocks({
        "physical_page": 5, "width": 560, "height": 842,
        "elements": [
            _line("本规程用词说明 …………58", 100),
            _line("附录A 调查表........61", 130),
        ],
        "regions": [
            {"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 120]},
            {"region_id": "r2", "model_label": "text", "bbox": [45, 125, 520, 150]},
        ],
    })
    assert {block["_v3_role"] for block in toc} == {"toc"}

    prose = build_page_blocks({
        "physical_page": 12, "width": 560, "height": 842,
        "elements": [_line("如果管道堵塞……应及时疏通。", 100)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 125]}],
    })
    assert prose[0]["_v3_role"] == "paragraph"


def test_cover_before_first_structural_page_is_front_matter_without_filename_rule() -> None:
    doc = prepare_page_document_v3({
        "sha256": "b" * 64, "file_name": "arbitrary-name.pdf", "pages": [
            {
                "physical_page": 1, "width": 560, "height": 842,
                "elements": [_line("中 国 工 程 建 设 协 会 标 准", 120)],
                "regions": [{"region_id": "cover", "model_label": "doc_title",
                             "bbox": [80, 100, 480, 180]}],
            },
            {
                "physical_page": 2, "width": 560, "height": 842,
                "elements": [_line("前言", 100)],
                "regions": [{"region_id": "preface", "model_label": "paragraph_title",
                             "bbox": [220, 90, 340, 130]}],
            },
        ],
    })
    assert doc["pages"][0]["elements"][0]["_v3_role"] == "front_matter"
    assert doc["pages"][1]["elements"][0]["_v3_role"] == "heading"


def _body_page(number: int, text: str, region_label: str = "text") -> dict:
    return {
        "physical_page": number, "width": 560, "height": 842,
        "elements": [_line(text, 300)],
        "regions": [
            {"region_id": f"h{number}", "model_label": "header", "bbox": [45, 20, 520, 40]},
            {"region_id": f"t{number}", "model_label": region_label, "bbox": [45, 295, 520, 325]},
        ],
    }


def test_spaced_preface_heading_bounds_front_matter() -> None:
    doc = prepare_page_document_v3({
        "sha256": "c" * 64, "file_name": "arbitrary-name.pdf", "pages": [
            _body_page(1, "某省工程建设地方标准", "doc_title"),
            _body_page(2, "前 言"),
            _body_page(3, "本标准适用于新建和改建工程。"),
        ],
    })
    roles = [page["elements"][0]["_v3_role"] for page in doc["pages"]]
    assert roles[0] == "front_matter"
    assert "front_matter" not in roles[1:]


def test_document_without_structural_start_has_no_front_matter() -> None:
    doc = prepare_page_document_v3({
        "sha256": "d" * 64, "file_name": "policy.pdf", "pages": [
            _body_page(1, "关于加强城市生活污水管网建设的通知", "doc_title"),
            _body_page(2, "一、总体要求"),
            _body_page(3, "各地要加快补齐管网短板。"),
        ],
    })
    assert all(page["elements"][0]["_v3_role"] != "front_matter" for page in doc["pages"])


def test_isolated_adjacent_formula_number_is_consumed_by_formula() -> None:
    formula = _PageItem(kind="formula", page=7, bbox=[80, 200, 380, 240],
                        extra={"latex": "Q=A v"}, source_index=0)
    number = _PageItem(kind="text", page=7, bbox=[390, 210, 430, 230],
                       text="(5.4.1-2)", source_index=1)
    unrelated = _PageItem(kind="text", page=7, bbox=[80, 300, 120, 320],
                          text="(3)", source_index=2)
    consumed = _associate_adjacent_formula_numbers([formula, number, unrelated])
    assert consumed == {1}
    assert formula.extra["adjacent_formula_number"] == "5.4.1-2"
    assert "adjacent_formula_number" not in unrelated.extra

    far_same_row = _PageItem(kind="text", page=7, bbox=[900, 210, 930, 230],
                             text="(6)", source_index=3)
    assert _associate_adjacent_formula_numbers([formula, far_same_row]) == set()


def test_split_native_number_prefix_and_appendix_hierarchy() -> None:
    page = {
        "physical_page": 13, "width": 560, "height": 842,
        "elements": [{**_line("9.\n3.\n4 应利用排水管网监测数据。", 150), "bbox": [68, 150, 532, 182]}],
        "regions": [{"region_id": "r", "model_label": "text", "bbox": [60, 145, 540, 190]}],
    }
    blocks = build_page_blocks(page)
    assert blocks[0]["text"] == "9.3.4 应利用排水管网监测数据。"
    assert blocks[0]["_v3_heading"]["label"] == "9.3.4"
    span_page = {
        "physical_page": 13, "width": 560, "height": 842,
        "elements": [_line("9.", 150, 68), _line("3.", 150, 82),
                     _line("4 应利用排水管网监测数据。", 150, 96)],
        "regions": [{"region_id": "r", "model_label": "text", "bbox": [60, 145, 540, 170]}],
    }
    span_blocks = build_page_blocks(span_page)
    assert span_blocks[0]["text"] == "9.3.4 应利用排水管网监测数据。"
    assert heading_descriptor("附录B 暴雨强度公式的编制方法", is_title=True) == {
        "label": "附录B", "title": "暴雨强度公式的编制方法", "level": 1}
    assert heading_descriptor("B.0.1 本方法适用于自记雨量记录。", is_clause=True) == {
        "label": "B.0.1", "title": None, "level": 3}


def test_r2b_baseline_snapshot_is_explicit_and_validated(tmp_path) -> None:
    snapshot = tmp_path / "baseline.json"
    snapshot.write_text(
        '{"schema_version":"middle-layer-v3-r2b-baseline-snapshot/1",'
        '"source_commit":"0f209d9","adjacent_repetition_rows":[],'
        '"appendix_clause_rows":[],"body_clause_duplicates_by_document":{}}',
        encoding="utf-8",
    )
    assert _read_r2b_baseline_snapshot(snapshot)["source_commit"] == "0f209d9"

    snapshot.write_text(
        '{"schema_version":"middle-layer-v3-r2b-baseline-snapshot/1",'
        '"source_commit":"wrong","adjacent_repetition_rows":[],'
        '"appendix_clause_rows":[],"body_clause_duplicates_by_document":{}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source_commit"):
        _read_r2b_baseline_snapshot(snapshot)
