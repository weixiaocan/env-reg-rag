from __future__ import annotations

import numpy as np

from src.ingestion.ocr_preprocessing import PreprocessConfig, preprocess_ocr_image
from src.ingestion.ocr_region_text import assemble_page_region_text


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


def test_region_assembly_uses_pp_lines_and_primary_only_vertical_fallback() -> None:
    page = _page()
    page["elements"] = [
        {"element_id": "p1", "type": "text", "text": "主 OCR 同行", "bbox": [50, 100, 200, 114]},
        {"element_id": "p2", "type": "text", "text": "主 OCR 独有", "bbox": [50, 150, 200, 164]},
    ]
    pp = {"lines": [_line("PP 干净同行", [50, 101, 210, 115])]}
    result, stats = assemble_page_region_text(
        page, pp, run_id="ocrv6-test", run_manifest_sha256="a" * 64,
        preprocessing={"enabled": True, "gray_threshold": 120},
        watermarks=[],
    )
    assert [row["text"] for row in result["elements"]] == ["PP 干净同行", "主 OCR 独有"]
    assert [row["_ocr_text_source"]["source"] for row in result["elements"]] == [
        "pp_clean", "primary_fallback"]
    assert stats["primary_fallback_lines"] == 1


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
        page, pp, run_id="ocrv6-test", run_manifest_sha256="b" * 64,
        preprocessing={"enabled": True}, watermarks=["住房城乡建设部"],
    )
    assert [row["text"] for row in result["elements"]] == ["4 完善管网体系"]
    assert result["elements"][0]["_ocr_text_source"]["primary_prefix_preserved"] is True
    assert stats["noise_lines"] == 2


def test_region_assembly_uses_primary_prefix_when_pp_merged_an_extra_digit() -> None:
    page = _page()
    page["elements"] = [{
        "element_id": "p1", "type": "text", "text": "4.1.5", "bbox": [50, 100, 90, 114],
    }]
    pp = {"lines": [_line("4.1.51程建设施工降水不应排入市政污水管道", [50, 100, 330, 114])]}
    result, stats = assemble_page_region_text(
        page, pp, run_id="ocrv6-test", run_manifest_sha256="d" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert result["elements"][0]["text"].startswith("4.1.5 程建设")
    assert stats["number_prefixes_preserved"] == 1


def test_pp_line_outside_regions_becomes_standalone_synthetic_region() -> None:
    page = _page()
    pp = {"lines": [_line("2.1.1 独立条文。", [50, 300, 260, 314])]}
    result, stats = assemble_page_region_text(
        page, pp, run_id="ocrv6-test", run_manifest_sha256="c" * 64,
        preprocessing={"enabled": True}, watermarks=[],
    )
    assert stats["orphan_pp_lines"] == 1
    assert result["elements"][0]["_v3_assigned_region_id"].startswith("ocr-orphan-")
    assert any(r["region_id"].startswith("ocr-orphan-") for r in result["regions"])


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
