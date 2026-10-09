from src.evaluation.independent_ocr_verifier import mapped_bbox, verify_text_region


def test_rotated_bbox_maps_back_to_original_page_coordinates():
    # Original 100 x 200 image rotated clockwise (k=3).  The original box
    # [10, 20, 30, 40] maps to rotated points [(160,10), (180,30)].
    box = mapped_bbox([[160, 10], [180, 10], [180, 30], [160, 30]],
                       rotation_k=3, original_width_px=100,
                       original_height_px=200, scale=1.0)
    assert box == [10.0, 20.0, 30.0, 40.0]


def test_only_strict_high_confidence_region_match_auto_passes():
    region = {"region_id": "r1", "bbox": [0, 0, 100, 40], "text": "第 3 条：10%"}
    lines = [
        {"line_id": "l1", "bbox": [1, 1, 50, 18], "text": "第3条：", "score": 0.99},
        {"line_id": "l2", "bbox": [1, 20, 40, 38], "text": "10%", "score": 0.98},
    ]
    result = verify_text_region(region, lines)
    assert result["automatic_conclusion"] == "verified_passed"
    assert result["verification_authority"] == "automatic_local_model_comparison"

    lines[1]["text"] = "1O%"
    result = verify_text_region(region, lines)
    assert result["automatic_conclusion"] == "manual_review_required"


def test_low_confidence_exact_match_does_not_auto_pass():
    region = {"region_id": "r1", "bbox": [0, 0, 100, 40], "text": "10%"}
    lines = [{"line_id": "l1", "bbox": [1, 1, 40, 20], "text": "10%", "score": 0.90}]
    result = verify_text_region(region, lines)
    assert result["strict_text_match"] is True
    assert result["automatic_conclusion"] == "manual_review_required"


def test_unique_contiguous_exact_subset_ignores_neighbor_lines_from_loose_boundary():
    region = {"region_id": "r-subset", "bbox": [0, 0, 200, 100], "text": "Target text"}
    lines = [
        {"line_id": "l1", "bbox": [0, 0, 100, 20], "text": "Header", "score": 0.99},
        {"line_id": "l2", "bbox": [0, 30, 100, 50], "text": "Target", "score": 0.99},
        {"line_id": "l3", "bbox": [0, 50, 100, 70], "text": " text", "score": 0.98},
        {"line_id": "l4", "bbox": [0, 80, 100, 100], "text": "Footer", "score": 0.99},
    ]

    result = verify_text_region(region, lines)

    assert result["automatic_conclusion"] == "verified_passed"
    assert result["strict_text_match"] is True
    assert result["alignment_mode"] == "unique_contiguous_exact_subset"
    assert result["matched_line_ids"] == ["l2", "l3"]


def test_multiple_exact_subsets_stay_ambiguous():
    region = {"region_id": "r-ambiguous", "bbox": [0, 0, 200, 100], "text": "same"}
    lines = [
        {"line_id": "l1", "bbox": [0, 0, 100, 20], "text": "same", "score": 0.99},
        {"line_id": "l2", "bbox": [0, 40, 100, 60], "text": "same", "score": 0.99},
    ]

    result = verify_text_region(region, lines)

    assert result["automatic_conclusion"] == "manual_review_required"
    assert result["alignment_mode"] == "ambiguous_contiguous_exact_subsets"
