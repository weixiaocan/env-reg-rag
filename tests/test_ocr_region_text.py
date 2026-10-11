from __future__ import annotations

import numpy as np

from src.ingestion.ocr_preprocessing import PreprocessConfig, preprocess_ocr_image
from src.ingestion.ocr_region_text import (
    assemble_page_region_text,
    find_adjacent_duplicate_fragments,
    merge_source_texts,
)


def _line(text: str, bbox: list[float], *, score: float = 0.99, line_id: str = "l1") -> dict:
    return {"line_id": line_id, "text": text, "bbox": bbox, "score": score}


def _page() -> dict:
    return {
        "physical_page": 1,
        "page_index": 0,
        "width": 400,
        "height": 600,
        "extraction_route": "full_ocr",
        "elements": [],
        "regions": [{
            "region_id": "r1", "kind": "text", "model_label": "text",
            "bbox": [40, 80, 360, 220],
        }],
    }


def test_region_assembly_prefers_primary_when_clean_conflicts_and_raw_is_missing() -> None:
    page = _page()
    page["elements"] = [
        {"element_id": "p1", "type": "text", "text": "主 OCR 同行", "bbox": [50, 100, 200, 114]},
        {"element_id": "p2", "type": "text", "text": "主 OCR 独有", "bbox": [50, 150, 200, 164]},
    ]
    pp = {"lines": [_line("PP 干净同行", [50, 101, 210, 115])]}
    result, stats = assemble_page_region_text(
        page, pp, {"lines": []}, run_id="ocrv6-test", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="e" * 64,
        preprocessing={"enabled": True, "gray_threshold": 120},
        watermarks=[],
    )
    assert [row["text"] for row in result["elements"]] == ["主 OCR 同行", "主 OCR 独有"]
    assert [row["_ocr_text_source"]["rule"] for row in result["elements"]] == [
        "merge", "single_source"]
    assert stats["single_source_lines"] == 1


def test_region_assembly_filters_noise_and_preserves_primary_number_prefix() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "4 完善管网体系", "bbox": [50, 100, 230, 114],
    }]
    pp = {"lines": [
        _line("完善管网体系", [50, 100, 230, 114], line_id="l1"),
        _line("住房城乡建设部", [50, 130, 200, 144], line_id="l2"),
        _line("金", [50, 160, 65, 174], score=0.4, line_id="l3"),
    ]}
    result, stats = assemble_page_region_text(
        page, pp, {"lines": []}, run_id="ocrv6-test", run_manifest_sha256="b" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="e" * 64,
        preprocessing={"enabled": True}, watermarks=["住房城乡建设部"],
    )
    assert [row["text"] for row in result["elements"]] == ["4 完善管网体系"]
    assert result["elements"][0]["_ocr_text_source"]["primary_prefix_preserved"] is False
    assert stats["noise_lines"] == 2


def test_region_assembly_uses_primary_prefix_when_pp_merged_an_extra_digit() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "4.1.5", "bbox": [50, 100, 90, 114],
    }]
    pp = {"lines": [_line("4.1.51程建设施工降水不应排入市政污水管道", [50, 100, 330, 114])]}
    result, stats = assemble_page_region_text(
        page, pp, {"lines": []}, run_id="ocrv6-test", run_manifest_sha256="d" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="e" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"][0]["text"].startswith("4.1.5 程建设")
    assert stats["number_prefixes_preserved"] == 1


def test_pp_line_outside_regions_becomes_standalone_synthetic_region() -> None:
    page = _page()
    pp = {"lines": [_line("2.1.1 独立条文。", [50, 300, 260, 314])]}
    result, stats = assemble_page_region_text(
        page, pp, {"lines": []}, run_id="ocrv6-test", run_manifest_sha256="c" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="e" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert stats["orphan_line_groups"] == 1
    assert result["elements"][0]["_v3_assigned_region_id"].startswith("ocr-orphan-")
    assert any(r["region_id"].startswith("ocr-orphan-") for r in result["regions"])


def test_three_source_whole_line_majority() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "城镇竖向规划",
        "bbox": [50, 100, 230, 114],
    }]
    clean = {"lines": [_line("城镇紧向规划", [50, 100, 230, 114])]}
    raw = {"lines": [_line("城镇竖向规划", [50, 100, 230, 114])]}
    result, stats = assemble_page_region_text(
        page, clean, raw, run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"][0]["text"] == "城镇竖向规划"
    assert result["elements"][0]["_ocr_text_source"]["rule"] == "majority"
    assert stats["majority_lines"] == 1


def test_three_source_character_merge_recovers_semantic_characters() -> None:
    cases = [
        ("安全等级不应低于级", "安全等级不应低于二级", "安全等级不应低于二级", "安全等级不应低于二级"),
        ("城镇紧向规划", "城镇竖向规划", "城镇竖向", "城镇竖向规划"),
        ("烟(净化", "烟气净化", "烟气", "烟气净化"),
    ]
    for clean_text, raw_text, primary_text, expected in cases:
        page = _page()
        page["elements"] = [{
            "element_id": "p1", "type": "text", "text": primary_text,
            "bbox": [50, 100, 230, 114],
        }]
        result, _ = assemble_page_region_text(
            page, {"lines": [_line(clean_text, [50, 100, 230, 114])]},
            {"lines": [_line(raw_text, [50, 100, 230, 114])]},
            run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
            raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
            preprocessing={"enabled": True}, watermarks=[],
        )
        assert result["elements"][0]["text"] == expected


def test_three_source_merge_keeps_fragment_present_in_only_one_source() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "2.2.13 地下干管",
        "bbox": [50, 100, 230, 114],
    }]
    result, _ = assemble_page_region_text(
        page, {"lines": [_line("2.2.13 地下干管安全等级不应低于二级", [50, 100, 330, 114])]},
        {"lines": [_line("安全等级不应低于二级", [180, 100, 330, 114])]},
        run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"][0]["text"] == "2.2.13 地下干管安全等级不应低于二级"


def test_three_source_two_way_conflict_prefers_raw_then_primary_then_clean() -> None:
    page = _page()
    page["elements"] = []
    result, stats = assemble_page_region_text(
        page, {"lines": [_line("施工现扬", [50, 100, 230, 114])]},
        {"lines": [_line("施工现场", [50, 100, 230, 114])]},
        run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"][0]["text"] == "施工现场"
    provenance = result["elements"][0]["_ocr_text_source"]
    assert provenance["rule"] == "merge"
    assert provenance["conflict_fragments"]
    assert stats["conflict_fragment_count"] >= 1


def test_only_clean_line_is_marked_single_source_clean() -> None:
    page = _page()
    result, stats = assemble_page_region_text(
        page, {"lines": [_line("仅清洗源召回", [50, 100, 230, 114])]}, {"lines": []},
        run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    provenance = result["elements"][0]["_ocr_text_source"]
    assert provenance["rule"] == "single_source"
    assert provenance["single_source"] == "pp_clean"
    assert stats["single_source_pp_clean_lines"] == 1


def test_raw_only_line_and_fragment_are_not_introduced() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "服务人口大于20万",
        "bbox": [50, 100, 230, 114],
    }]
    clean = {"lines": [_line("服务人口大于20万", [50, 100, 230, 114])]}
    raw = {"lines": [
        _line("服务人口大于20)万", [50, 100, 230, 114], line_id="raw-body"),
        _line("公开", [50, 150, 90, 164], line_id="raw-watermark"),
    ]}
    result, stats = assemble_page_region_text(
        page, clean, raw, run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert [row["text"] for row in result["elements"]] == ["服务人口大于20万"]
    assert stats["suppressed_pp_raw_single_source_lines"] == 1


def test_raw_only_insert_fragment_is_not_introduced_during_merge() -> None:
    merged, rule, _ = merge_source_texts({
        "pp_clean": "服务人口大于20)万的城镇排水工程",
        "pp_raw": "服务人口大于20万的城镇排水工程",
        "primary": None,
    })
    assert rule == "merge"
    assert merged == "服务人口大于20万的城镇排水工程"


def test_raw_only_insert_is_rejected_when_clean_source_has_no_character() -> None:
    merged, rule, _ = merge_source_texts({
        "pp_clean": "服务人口大于20万的城镇排水工程",
        "pp_raw": "服务人口大于20)万的城镇排水工程",
        "primary": "服务人口大于20万的城镇排水",
    })
    assert rule == "merge"
    assert merged == "服务人口大于20万的城镇排水工程"


def test_short_raw_primary_watermark_strokes_are_rejected_without_clean_vote() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "IZ",
        "bbox": [50, 100, 80, 114],
    }]
    result, stats = assemble_page_region_text(
        page, {"lines": []}, {"lines": [_line("LIT", [50, 100, 80, 114])]},
        run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"] == []
    assert stats["suppressed_ambiguous_short_lines"] == 1


def test_watermark_phrase_substrings_are_noise_only_as_whole_lines() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "设备应采用专用工具",
        "bbox": [50, 100, 230, 114],
    }]
    clean = {"lines": [
        _line("设备应采用专用工具", [50, 100, 230, 114], line_id="body"),
        _line("公开", [50, 130, 90, 144], line_id="public"),
        _line("浏览", [50, 150, 90, 164], line_id="browse"),
        _line("专用", [50, 170, 90, 184], line_id="special"),
    ]}
    result, stats = assemble_page_region_text(
        page, clean, {"lines": []}, run_id="ocrv6-clean",
        run_manifest_sha256="a" * 64, raw_run_id="ocrv6-raw",
        raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=["信息公开", "浏览专用"],
    )
    assert [row["text"] for row in result["elements"]] == ["设备应采用专用工具"]
    assert stats["noise_lines"] == 3


def test_review_case_split_primary_tail_does_not_duplicate_planning_phrase() -> None:
    page = _page()
    page["elements"] = [
        {"element_id": "p1", "type": "text",
         "text": "2.1.10乡村污水系统应以县级行政区域",
         "bbox": [50, 100, 235, 114]},
        {"element_id": "p2", "type": "text", "text": "应实行统一规划，",
         "bbox": [265, 100, 345, 114]},
    ]
    clean = {"lines": [_line(
        "2.1.10 乡村污水系统应以县级行政区域为单位实行统一规划.",
        [50, 100, 345, 114])]
    }
    raw = {"lines": [_line(
        "2.1.10 乡村污水系统应以县级行政区域为单位实行统一规划，",
        [50, 100, 345, 114])]
    }
    result, stats = assemble_page_region_text(
        page, clean, raw, run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert [row["text"] for row in result["elements"]] == [
        "2.1.10 乡村污水系统应以县级行政区域为单位实行统一规划，"]
    assert stats["merge_invariant_violation_count"] == 0


def test_review_case_replace_uses_primary_character_without_appending_it() -> None:
    merged, rule, _ = merge_source_texts({
        "pp_clean": "2.2.13 排水工程主要构筑物的主体结构和地下F管，其结构设",
        "pp_raw": None,
        "primary": "2.2.13排水工程主要构筑物的主体结构和地下干",
    })
    assert rule == "merge"
    assert merged == "2.2.13 排水工程主要构筑物的主体结构和地下干管，其结构设"
    assert "F" not in merged
    assert not merged.endswith("干")


def test_review_case_overlapping_primary_fragments_are_not_reinserted() -> None:
    page = _page()
    page["elements"] = [
        {"element_id": "p1", "type": "text", "text": "井盖和雨水箅缺失或损坏信息后，",
         "bbox": [50, 100, 215, 114]},
        {"element_id": "p2", "type": "text", "text": "必须在2h内安放护栏和警示",
         "bbox": [208, 99, 345, 115]},
    ]
    full = "井盖和雨水箅缺失或损坏信息后，必须在2h内安放护栏和警示"
    result, stats = assemble_page_region_text(
        page, {"lines": [_line(full, [50, 100, 345, 114])]},
        {"lines": [_line(full, [50, 100, 345, 114])]},
        run_id="ocrv6-clean", run_manifest_sha256="a" * 64,
        raw_run_id="ocrv6-raw", raw_run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert [row["text"] for row in result["elements"]] == [full]
    assert stats["merge_invariant_violation_count"] == 0


def test_adjacent_duplicate_invariant_reports_only_merge_introduced_repeat() -> None:
    fragment = "应实行统一规划"
    sources = {
        "pp_clean": f"行政区域为单位{fragment}，",
        "pp_raw": f"行政区域为单位{fragment}，",
        "primary": "行政区域",
    }
    violations = find_adjacent_duplicate_fragments(
        f"行政区域为单位{fragment}，{fragment}，", sources)
    assert violations == [{"fragment": fragment, "separator": "，", "length": len(fragment)}]
    assert find_adjacent_duplicate_fragments(
        f"{fragment}，{fragment}，", {"pp_raw": f"{fragment}，{fragment}，"}) == []


def test_preprocess_removes_light_gray_and_red_but_keeps_black() -> None:
    image = np.full((3, 7, 3), 255, dtype=np.uint8)
    image[0, 0] = [150, 150, 150]
    image[0, 3] = [20, 20, 20]
    image[0, 6] = [210, 40, 40]
    cleaned, stats = preprocess_ocr_image(
        image, PreprocessConfig(gray_threshold=120, red_min=140, red_delta=60))
    assert cleaned[0, 0].tolist() == [255, 255, 255]
    assert cleaned[0, 3].tolist() == [20, 20, 20]
    assert cleaned[0, 6].tolist() == [255, 255, 255]
    assert stats["gray_pixels_whitened"] == 1
    assert stats["red_pixels_whitened"] == 1


def test_trusted_full_line_beats_majority_on_a_fragment() -> None:
    merged, rule, _ = merge_source_texts({
        "pp_clean": "2.1.15 植草沟grass swale", "pp_raw": "grass swale", "primary": "grass swale"})
    assert (merged, rule) == ("2.1.15 植草沟grass swale", "superset_over_fragment_majority")


def test_pp_raw_full_line_never_overrides_majority() -> None:
    merged, rule, _ = merge_source_texts({
        "pp_clean": "排水管道", "pp_raw": "公开排水管道", "primary": "排水管道"})
    assert (merged, rule) == ("排水管道", "majority")
