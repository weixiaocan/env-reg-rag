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
    _drop_fragments_inside_superset_lines,
    assemble_page_region_text,
    find_adjacent_duplicate_fragments,
    merge_source_texts,
    recover_source_supported_occlusion_fragments,
    remove_introduced_adjacent_duplicate_fragments,
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


def test_three_part_clause_split_tolerates_gaps_and_ignores_prose_numbers() -> None:
    lines = [_line("7.6.31 第一条。", 100), _line("0.5 m/s 时取上限。", 115),
             _line("7.6.33 第三条。", 130)]
    assert [len(group) for group in split_clause_lines(lines)] == [2, 1]


def _labels(blocks: list[dict]) -> list[str]:
    return [str((block.get("_v3_heading") or {}).get("label") or "") for block in blocks]


def test_clause_glued_to_latin_term_is_a_clause_start() -> None:
    blocks = build_page_blocks({
        "physical_page": 9, "width": 560, "height": 842,
        "elements": [_line("7.6.32 前一条正文。", 100), _line("7.6.33SBR反应池的数量不宜少于2个。", 130)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 150]}],
    })
    assert "7.6.33" in _labels(blocks)
    assert not any(_labels(build_page_blocks({
        "physical_page": 9, "width": 560, "height": 842,
        "elements": [_line("1.5m 以下的管道应加强巡查。", 100)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 125]}],
    })))


def test_inline_clause_after_sentence_end_and_after_continuation_splits() -> None:
    inline = build_page_blocks({
        "physical_page": 12, "width": 560, "height": 842,
        "elements": [_line("3.1.1 规划内容包括污水处理要求。3.1.2 规划期限宜与总体规划一致。", 100)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 125]}],
    })
    assert _labels(inline)[:2] == ["3.1.1", "3.1.2"]
    continuation = build_page_blocks({
        "physical_page": 10, "width": 560, "height": 842,
        "elements": [_line("建筑雨水立管旱天有污水流出。\n5.5.4 确认混错接点时应实地标注。", 100)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 125]}],
    })
    assert "5.5.4" in _labels(continuation)
    reference = build_page_blocks({
        "physical_page": 30, "width": 560, "height": 842,
        "elements": [_line("应按本标准公式(4.1.23)计算，并符合第4.1.24条的规定。", 100)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 125]}],
    })
    assert not any(_labels(reference))


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


def _vote_page(primary: list[tuple[str, list[float]]],
               paddle: list[tuple[str, list[float]]]) -> tuple[dict, dict, dict]:
    page = {
        "physical_page": 57, "width": 400, "height": 600,
        "regions": [{"region_id": "body", "kind": "text", "model_label": "text",
                     "bbox": [40, 90, 360, 200]}],
        "elements": [{"element_id": f"p{index}", "type": "text", "text": text, "bbox": bbox}
                     for index, (text, bbox) in enumerate(primary)],
    }
    lines = [{"line_id": f"l{index}", "text": text, "bbox": bbox, "score": 0.99}
             for index, (text, bbox) in enumerate(paddle)]
    return page, {"lines": lines}, {"lines": [dict(line) for line in lines]}


def _vote_texts(page: dict, clean: dict, raw: dict) -> list[str]:
    result, _ = assemble_page_region_text(
        page, clean, raw, run_id="r", run_manifest_sha256="m", raw_run_id="rr",
        raw_run_manifest_sha256="rm", preprocessing={}, watermarks=[])
    return [row["text"] for row in result["elements"]]


def test_vote_drops_engine_fragment_already_read_by_a_voted_line() -> None:
    # MinerU split line two into "运…小于：" and "排砂管应"; only the second
    # joined the Paddle group, so the first used to be appended again.
    page, clean, raw = _vote_page(
        [("7.4.7沉砂池除砂宜采用机械方法，并经砂水分", [55, 124.5, 300, 137.5]),
         ("运。当采用人工排砂时，排砂管直径不应小于：", [57.1, 142.6, 266.4, 150.7]),
         ("排砂管应", [300, 142, 351, 151])],
        [("7.4.7 沉砂池除砂宜采用机械方法，并经砂水分离后储存或外", [55, 124.5, 351, 137.5]),
         ("运。当采用人工排砂时，排砂管直径不应小于200mm。排砂管应", [54.5, 139, 351, 154.5])],
    )
    assert _vote_texts(page, clean, raw) == [
        "7.4.7 沉砂池除砂宜采用机械方法，并经砂水分离后储存或外",
        "运。当采用人工排砂时，排砂管直径不应小于200mm。排砂管应",
    ]


def test_vote_keeps_short_engine_piece_inside_a_voted_line() -> None:
    # "n——" shares its letter with the voted line but is not a repeat of it.
    def row(text: str, bbox: list[float], rule: str) -> dict:
        return {"text": text, "bbox": bbox, "_ocr_text_source": {"rule": rule}}
    voted = row("n测定次数(次/d)，每日测量6次～8次", [78, 111.5, 347, 123.5], "majority")
    piece = row("n——", [78, 112, 95, 123], "single_source")
    repeat = row("测定次数(次/d)，每日测量6次", [90, 112, 300, 123], "single_source")
    kept = _drop_fragments_inside_superset_lines([voted, piece, repeat], [], {
        "dropped_fragment_inside_superset_lines": 0})
    assert kept == [voted, piece]

    # MinerU boxed the repeat between two rows: still the adjacent line again.
    above = row("际产泥率进行预测；无资料时可结合污水水质、泥龄、工艺等因", [46.5, 350.5, 347, 370], "majority")
    last = row("素，按处理万立方米污水产含水率80%的污泥6t～9t估算。", [46.5, 375.5, 347, 389], "merge")
    between = row("素，按处理万立方米污水产含水率80%的污泥6t", [47, 363.3, 347, 376.1], "single_source")
    kept = _drop_fragments_inside_superset_lines([above, between, last], [], {
        "dropped_fragment_inside_superset_lines": 0})
    assert kept == [above, last]


def test_vote_drops_fragment_that_no_line_covers_only_when_text_differs() -> None:
    page, clean, raw = _vote_page(
        [("氨氮、总氮、总磷", [55, 160, 200, 172])],
        [("7.4.8 出水水质应符合下列规定：", [55, 124.5, 351, 137.5])],
    )
    assert _vote_texts(page, clean, raw) == ["7.4.8 出水水质应符合下列规定：", "氨氮、总氮、总磷"]


def test_vote_keeps_clause_number_both_paddle_runs_agree_on() -> None:
    page, clean, raw = _vote_page(
        [("7.5.101", [55, 100, 90, 112])],
        [("7.5.10 平流沉淀池的设计应符合下列规定：", [55, 100, 340, 112])],
    )
    assert _vote_texts(page, clean, raw) == ["7.5.10 平流沉淀池的设计应符合下列规定："]
    # A number only MinerU read still survives.
    page, clean, raw = _vote_page(
        [("7.5.10", [55, 100, 90, 112])],
        [("平流沉淀池的设计应符合下列规定：", [55, 100, 340, 112])],
    )
    assert _vote_texts(page, clean, raw) == ["7.5.10 平流沉淀池的设计应符合下列规定："]


def test_short_last_line_under_a_tall_ocr_box_stays_on_its_own_row() -> None:
    page = {
        "physical_page": 145, "width": 400, "height": 600,
        "regions": [{"region_id": "body", "kind": "text", "model_label": "text",
                     "bbox": [50, 205, 360, 260]}],
        "elements": [
            {"element_id": "a", "type": "text", "bbox": [57.5, 209.5, 354.0, 232.5],
             "text": "压实虚厚，管节（段）沉放前应再次清除槽底回淤、异物；在基",
             "_v3_assigned_region_id": "body"},
            {"element_id": "b", "type": "text", "bbox": [56.5, 225.0, 353.5, 247.0],
             "text": "槽断面方向两侧可打两排短桩设置高程导轨，便于控制基础整平",
             "_v3_assigned_region_id": "body"},
            {"element_id": "c", "type": "text", "bbox": [54.0, 239.0, 86.5, 257.0],
             "text": "施工；", "_v3_assigned_region_id": "body"},
        ],
        "_v3_ocr_region_text": True,
    }
    text = build_page_blocks(page)[0]["text"]
    assert text.endswith("便于控制基础整平\n施工；")


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


def test_heading_split_one_character_per_line_bounds_front_matter() -> None:
    doc = prepare_page_document_v3({
        "sha256": "e" * 64, "file_name": "arbitrary-name.pdf", "pages": [
            _body_page(1, "某市地方标准", "doc_title"),
            _body_page(2, "目\n次 \n"),
            _body_page(3, " \n前\n言 \n"),
            _body_page(4, "本文件由某市水务局提出并归口。"),
        ],
    })
    roles = [page["elements"][0]["_v3_role"] for page in doc["pages"]]
    assert roles[0] == "front_matter"
    assert "front_matter" not in roles[1:]


def test_contents_page_before_the_preface_stays_navigation() -> None:
    doc = prepare_page_document_v3({
        "sha256": "f" * 64, "file_name": "arbitrary-name.pdf", "pages": [
            _body_page(1, "某某发展规划", "doc_title"),
            _body_page(2, "一、总体要求..........................7", "doc_title"),
            _body_page(3, "前 言"),
        ],
    })
    roles = [page["elements"][0]["_v3_role"] for page in doc["pages"]]
    assert roles[:2] == ["front_matter", "toc"]


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


def test_wrapped_cross_reference_is_not_a_clause_start() -> None:
    blocks = build_page_blocks({
        "physical_page": 68, "width": 560, "height": 842,
        "elements": [_line("5.10.3 防腐层应符合本规范第", 100), _line("5.10.4条的规定。", 115)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 520, 130]}],
    })
    assert _labels(blocks) == ["5.10.3"]


def test_number_only_heading_joins_following_term_heading() -> None:
    blocks = build_page_blocks({
        "physical_page": 5, "width": 595, "height": 842,
        "elements": [_line("2.1", 280), _line("降雨量 rainfall", 298), _line("从天空降到地面上的雨。", 316)],
        "regions": [
            {"region_id": "n", "model_label": "paragraph_title", "bbox": [60, 278, 90, 294]},
            {"region_id": "t", "model_label": "paragraph_title", "bbox": [85, 296, 170, 312]},
            {"region_id": "b", "model_label": "text", "bbox": [85, 314, 500, 330]},
        ],
    })
    assert blocks[0]["text"] == "2.1 降雨量 rainfall"
    assert blocks[0]["_v3_heading"]["label"] == "2.1"


def test_unnumbered_back_matter_title_is_a_top_level_heading() -> None:
    from src.ingestion.block_assembly import heading_descriptor
    assert heading_descriptor("参 考 文 献", is_title=True) == {
        "label": "参考文献", "title": None, "level": 1}
    assert heading_descriptor("参考文献", is_title=False) is None


def test_noisy_repeat_beside_its_voted_line_is_dropped() -> None:
    def row(text: str, bbox: list[float], rule: str) -> dict:
        return {"text": text, "bbox": bbox, "_ocr_text_source": {"rule": rule}}
    voted = row("3.0.4 城镇分流制排水系统雨污混接调查与治理的环节宜包括", [50, 100, 347, 113], "majority")
    noisy = row("城镇分流制排水系统雨污？昆接调查与治理的环节宜包括", [70, 108, 347, 121], "single_source")
    nextline = row("混接预判、资料收集与现场踏勘、混接区域调查", [50, 116, 347, 129], "majority")
    kept = _drop_fragments_inside_superset_lines([voted, noisy, nextline], [], {
        "dropped_fragment_inside_superset_lines": 0})
    assert kept == [voted, nextline]


def test_merge_keeps_mid_line_symbols_only_the_raw_run_read() -> None:
    # Watermark removal erased the thin '/' and '～'; pp_raw read them.
    assert merge_source_texts({"pp_clean": "应池容积的23。", "pp_raw": "应池容积的2/3",
                               "primary": None})[0] == "应池容积的2/3"
    assert merge_source_texts({
        "pp_clean": "块铺砌的明渠可采用1：(1.7-1：1的边坡。",
        "pp_raw": "块铺砌的明渠可采用1：0.75～1：1的边坡。", "primary": None,
    })[0] == "块铺砌的明渠可采用1：0.75～1：1的边坡。"
    # At the line end a raw-only digit is the next item number, not text.
    assert merge_source_texts({
        "pp_clean": "当种植其他植物时，其厚度不宜大于150mm：",
        "pp_raw": "当种植其他植物时，其厚度不宜大于150mm；4",
        "primary": "当种植其他植物时，其厚度不宜大于150mm；",
    })[0] == "当种植其他植物时，其厚度不宜大于150mm；"


def test_inline_formula_region_does_not_drop_its_body_line() -> None:
    page, clean, raw = _vote_page(
        [], [("度的管道外壁摩擦阻力（πDLfk）与工具管迎面阻力（NF）两", [58, 123, 354, 136]),
             ("部分之和。", [58, 138, 120, 152])])
    page["regions"].append({"region_id": "f1", "kind": "formula", "bbox": [169, 124, 213, 135]})
    assert _vote_texts(page, clean, raw) == [
        "度的管道外壁摩擦阻力（πDLfk）与工具管迎面阻力（NF）两", "部分之和。"]
    # A fragment the size of the region is the formula itself.
    page["regions"][-1]["bbox"] = [58, 123, 80, 136]
    clean["lines"].append({"line_id": "lf", "text": "ts", "bbox": [59, 124, 79, 135], "score": 0.99})
    assert _vote_texts(page, clean, raw) == [
        "度的管道外壁摩擦阻力（πDLfk）与工具管迎面阻力（NF）两", "部分之和。"]
    # A display formula region still owns the lines it covers.
    page["regions"][-1]["bbox"] = [55, 120, 360, 140]
    assert _vote_texts(page, clean, raw) == ["部分之和。"]


def test_figure_title_region_is_kept_as_a_caption_block() -> None:
    # A figure/table title is printed text: it stays in the corpus even when
    # no figure claims it (rotated or above-figure titles used to vanish).
    page = {
        "physical_page": 15, "width": 397, "height": 575,
        "elements": [_line("图3.0.4 混接调查与治理环节示意图", 300)],
        "regions": [{"region_id": "r1", "model_label": "figure_title",
                     "bbox": [45, 295, 350, 315]}],
    }
    blocks = build_page_blocks(page)
    assert [(b["_v3_role"], b["text"]) for b in blocks] == [
        ("caption", "图3.0.4 混接调查与治理环节示意图")]
    assert map_model_label("figure_title") == "caption"


def _slot(kind: str, index: int, bbox: list[float], text: str = "", **extra) -> _PageItem:
    return _PageItem(kind=kind, page=1, bbox=bbox, text=text, ref={"element_id": f"x{index}"},
                     source_index=index, extra=dict(extra))


def test_captionless_figures_take_the_nearest_figure_title() -> None:
    from src.application.canonical_assembly import _pair_layout_captions

    figures = [_slot("figure", 0, [160, 80, 245, 175]), _slot("figure", 1, [160, 220, 240, 310])]
    titles = [_slot("text", 2, [135, 190, 257, 199], "图1 雨水立管断接示意图（一）", v3_role="caption"),
              _slot("text", 3, [135, 319, 257, 328], "图2 雨水立管断接示意图（二）", v3_role="caption"),
              _slot("text", 4, [50, 400, 300, 412], "图中尺寸以毫米计。", v3_role="paragraph")]
    consumed: set[int] = set()
    consumed_by: dict[int, list] = {}
    _pair_layout_captions(figures + titles, consumed, consumed_by)
    assert figures[0].extra["layout_caption"] == "图1 雨水立管断接示意图（一）"
    assert figures[1].extra["layout_caption"] == "图2 雨水立管断接示意图（二）"
    assert consumed == {2, 3}
    assert consumed_by[0][0]["basis"] == "layout_figure_title_nearest"


def test_equidistant_titles_bind_nothing() -> None:
    from src.application.canonical_assembly import _pair_layout_captions

    figure = _slot("figure", 0, [100, 100, 200, 200])
    titles = [_slot("text", 1, [100, 210, 200, 220], "图1 甲", v3_role="caption"),
              _slot("text", 2, [100, 80, 200, 90], "图2 乙", v3_role="caption")]
    consumed: set[int] = set()
    _pair_layout_captions([figure] + titles, consumed, {})
    assert "layout_caption" not in figure.extra
    assert consumed == set()


def test_caption_line_inside_figure_box_is_not_dropped() -> None:
    from src.ingestion.ocr_region_text import _in_caption_region, _inside_structured

    image = {"kind": "image", "bbox": [56, 62, 342, 509]}
    title = {"kind": "text", "model_label": "figure_title", "bbox": [332, 212, 342, 362]}
    line = [331, 213, 342, 362]
    assert _inside_structured(line, "图3.0.4 混接调查与治理环节示意图", [image])
    assert _in_caption_region(line, [title])
    assert not _in_caption_region([100, 100, 200, 110], [title])


def test_only_a_table_recording_the_title_consumes_it() -> None:
    from src.application.canonical_assembly import _table_keeps_caption

    region = {"tables": [{"caption": "表7.1-2 单个混接点混接程度分级标准表"}]}
    assert _table_keeps_caption(region, "表7.1-2  单个混接点混接程度分级标准表")
    assert not _table_keeps_caption(region, "表7.1-2 中，污水流入水质的分级依据为：根据表5-1")
    assert not _table_keeps_caption({"tables": [{"caption": None}]}, "表4.1.5 内涝防治设计重现期")


def test_line_outside_every_layout_region_is_kept() -> None:
    page = {
        "physical_page": 5, "width": 595, "height": 842, "extraction_route": "native",
        "elements": [_line("5.1.1 条文正文。", 100), _line("发文字号：建城〔2024〕18号", 400)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 95, 350, 115]}],
    }
    texts = [b["text"] for b in build_page_blocks(page)]
    assert "发文字号：建城〔2024〕18号" in texts


def test_equation_numbers_stay_out_of_column_detection() -> None:
    page = {
        "physical_page": 24, "width": 397, "height": 575,
        "elements": [_line("4.1.22 截流井前合流管道的设计流量应按下式计算：", 186),
                     {"element_id": "n1", "text": "(4.1.22)", "bbox": [298, 204, 342, 216]},
                     _line("式中：Q——设计流量(L/s)；", 219)],
        "regions": [{"region_id": "r1", "model_label": "text", "bbox": [45, 180, 345, 200]},
                    {"region_id": "r2", "model_label": "formula_number", "bbox": [296, 202, 344, 218]},
                    {"region_id": "r3", "model_label": "text", "bbox": [45, 217, 290, 233]}],
    }
    blocks = build_page_blocks(page)
    assert [b["_v3_role"] for b in blocks][-1] == "formula_number"
    assert blocks[0]["text"].startswith("4.1.22")


def test_equation_number_binds_to_formula_on_its_row() -> None:
    from src.application.canonical_assembly import _associate_adjacent_formula_numbers

    formula = _slot("formula", 0, [156, 204, 235, 215])
    other = _slot("text", 1, [46, 219, 178, 232], "式中：Q——设计流量(L/s)；", v3_role="clause")
    number = _slot("text", 2, [298, 204, 342, 216], "(4. 1.22)", v3_role="formula_number")
    consumed = _associate_adjacent_formula_numbers([formula, other, number])
    assert consumed == {2}
    assert formula.extra["adjacent_formula_number"] == "4.1.22"


def test_partial_reading_does_not_cut_the_full_line() -> None:
    text, rule, _ = merge_source_texts({
        "pp_clean": "5.3.2 管道转弯和交接处，其水流转角不应小于90°。当管径小",
        "pp_raw": None, "primary": "当管径小"})
    assert text.startswith("5.3.2 管道转弯")
    assert rule == "superset_over_partial_reading"
    # pp_raw alone never wins on length: it carries watermark residue.
    text, rule, _ = merge_source_texts({"pp_clean": None, "pp_raw": "当管径小水印残留",
                                        "primary": "当管径小"})
    assert rule != "superset_over_partial_reading"


def test_overlap_dedup_keeps_whole_voted_name_row() -> None:
    from src.ingestion.block_assembly import _deduplicate_overlapping_lines

    parts = [
        {"text": "周质炎", "bbox": [251, 413, 285, 426]},
        {"text": "俞士静陆继诚周质炎", "bbox": [167, 413, 286, 427]},
        {"text": "陆继诚", "bbox": [210, 414, 243, 425]},
    ]
    kept = _deduplicate_overlapping_lines(parts)
    assert [item["text"] for item in kept] == ["俞士静陆继诚周质炎"]
