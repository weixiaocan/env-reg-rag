from __future__ import annotations

from src.evaluation.canonical_adjudication_pack import build_adjudication_items


def test_pack_deduplicates_to_unique_source_objects_and_attaches_canonical_impact():
    sha = "a" * 64
    region_id = "up-region-1"
    documents = [{
        "sha256": sha, "file_name": "source.pdf",
        "pages": [{"physical_page": 1, "regions": [{
            "region_id": region_id, "kind": "text", "bbox": [1, 2, 30, 40],
            "text": "candidate", "quality_status": "needs_review",
        }]}],
    }]
    canonicals = [{"elements": [{
        "element_id": "el-1", "type": "text", "content": {"text": "candidate"},
        "source_region_ids": [region_id], "quality_status": "needs_review",
    }]}]
    ledger = {"entries": [
        {"object_type": "source_document", "object_id": sha,
         "content_quality_conclusion": "manual_review_required", "source_identity": {"file_sha256": sha}},
        {"object_type": "source_region", "object_id": region_id,
         "content_quality_conclusion": "manual_review_required",
         "source_identity": {"file_sha256": sha, "physical_page": 1, "kind": "text",
                             "bbox": [1, 2, 30, 40]},
         "uncertainty": ["ocr_diff"], "evidence": {"methods": ["independent_ocr"]}},
        {"object_type": "canonical_element", "object_id": f"{sha}:el-1",
         "content_quality_conclusion": "manual_review_required",
         "source_identity": {"file_sha256": sha, "element_id": "el-1", "region_ids": [region_id]}},
        {"object_type": "source_signal", "object_id": "sig-1",
         "content_quality_conclusion": "manual_review_required",
         "source_identity": {"file_sha256": sha, "physical_page": 1,
                             "reason": "original_math_operator_candidate_not_assigned",
                             "bbox": [40, 50, 80, 70], "signal_id": "sig-1", "source_index": 2},
         "uncertainty": ["original_math_operator_candidate_not_assigned"],
         "evidence": {"methods": ["source_signal"], "details": {"original_text": "x=1"}}},
    ]}
    assets = {sha: {"canonical_rel_path": "source.pdf", "display_file_name": "source.pdf"}}

    items = build_adjudication_items(ledger, documents, canonicals, assets)

    assert [item["item_id"] for item in items] == [region_id, "sig-1"]
    region = items[0]
    assert region["impact"]["affected_canonical_element_count"] == 1
    assert region["impact"]["affected_canonical_elements"][0]["element_id"] == "el-1"
    signal = items[1]
    assert signal["kind"] == "formula"
    assert signal["extracted_result"]["original_text"] == "x=1"
