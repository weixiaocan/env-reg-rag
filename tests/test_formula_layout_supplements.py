import json
from pathlib import Path

import pytest

from src.ingestion.formula_layout import formula_regions
from src.ingestion.formula_supplements import SOURCE, supplemented_layout_boxes
from scripts.recognize_corpus_formulas import _reuse_unchanged_layout_checkpoints


SHA = "a" * 64


def _registry(root: Path, bbox=None):
    path = root / "data/registry/formula-layout-supplements.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "1", "regions": [{
        "source_sha256": SHA, "physical_page": 2,
        "bbox": bbox or [20, 30, 100, 50],
    }]}), encoding="utf-8")


def test_source_bound_supplement_produces_unscored_formula_region(tmp_path):
    _registry(tmp_path)
    boxes = supplemented_layout_boxes(tmp_path, SHA, 2, 200, 300, 400, 600, [])
    assert boxes[0]["coordinate"] == [40, 60, 200, 100]
    regions = formula_regions(boxes, 400, 600, 200, 300)
    assert regions[0]["bbox"] == [20, 30, 100, 50]
    assert regions[0]["score"] is None
    assert regions[0]["detection_source"] == SOURCE
    assert supplemented_layout_boxes(tmp_path, "b" * 64, 2, 200, 300, 400, 600, []) == []
    assert supplemented_layout_boxes(tmp_path, SHA, 1, 200, 300, 400, 600, []) == []


def test_supplement_out_of_page_and_overlap_fail_closed(tmp_path):
    _registry(tmp_path, [20, 30, 210, 50])
    with pytest.raises(ValueError, match="outside source page"):
        supplemented_layout_boxes(tmp_path, SHA, 2, 200, 300, 400, 600, [])
    _registry(tmp_path)
    with pytest.raises(ValueError, match="overlaps"):
        supplemented_layout_boxes(tmp_path, SHA, 2, 200, 300, 400, 600,
            [{"label": "formula", "coordinate": [40, 60, 200, 100], "score": .9}])


def test_only_unaffected_verified_local_checkpoints_are_rebound(tmp_path):
    _registry(tmp_path)
    old_hash, new_hash = "a" * 64, "b" * 64
    previous = tmp_path / "data/model_runtime/corpus_formulas" / old_hash[:16]
    previous.mkdir(parents=True)
    provenance = {"model_checksums": {"layout": "x"}, "package_versions": {"paddleocr": "x"}}
    for page in (1, 2):
        record = {"sha256": SHA, "physical_page": page, "config_hash": old_hash,
                  "status": "completed", "result": {"sha256": SHA,
                  "profile": "layout-formula-v1:200dpi:score0.5", **provenance,
                  "pages": [{"physical_page": page, "regions": []}]}}
        (previous / f"{SHA}-{page}.json").write_text(json.dumps(record), encoding="utf-8")
    current = tmp_path / "data/model_runtime/corpus_formulas" / new_hash[:16]
    count = _reuse_unchanged_layout_checkpoints(tmp_path, [(SHA, 1), (SHA, 2)],
        current, old_hash, new_hash, {(SHA, 2)}, provenance)
    assert count == 1
    reused = json.loads((current / f"{SHA}-1.json").read_text(encoding="utf-8"))
    assert reused["config_hash"] == new_hash
    assert reused["reused_from_run_id"] == old_hash[:16]
    assert not (current / f"{SHA}-2.json").exists()
