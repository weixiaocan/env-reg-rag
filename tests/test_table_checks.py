from __future__ import annotations

import json

import pytest

from src.application.table_checks import (
    TableCheckError,
    apply_table_checks,
    cells_sha256,
    load_table_checks,
)

SHA = "a" * 64


def _document(text: str = "球墨铸铁井盖、雨水算") -> dict:
    cells = [{"row": 0, "column": 0, "text": "井盖种类"},
             {"row": 1, "column": 0, "text": text}]
    return {"source": {"sha256": SHA}, "elements": [{
        "element_id": "e1", "type": "table", "source_region_ids": ["up-t1"],
        "quality_status": "needs_review", "policy_reasons": ["structured_default_locator_only"],
        "quality_issues": [], "content": {"cells": cells},
    }]}


def _registry(tmp_path, document: dict, **entry) -> dict:
    cells = document["elements"][0]["content"]["cells"]
    payload = {"schema_version": "1", "tables": [{
        "sha256": SHA, "region_id": "up-t1", "cells_sha256": cells_sha256(cells),
        "method": "text_layer_cell_check", "verdict": "admit",
        "corrections": [{"row": 1, "column": 0, "from": "球墨铸铁井盖、雨水算",
                         "to": "球墨铸铁井盖、雨水箅"}], **entry}]}
    path = tmp_path / "table-checks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_table_checks(path)


def test_admitted_table_takes_corrections_and_passes(tmp_path) -> None:
    document = _document()
    registry = _registry(tmp_path, document)
    assert apply_table_checks(document, registry) == 1
    table = document["elements"][0]
    assert table["content"]["cells"][1]["text"] == "球墨铸铁井盖、雨水箅"
    assert table["quality_status"] == "passed"
    assert "table_admitted" in table["policy_reasons"]
    assert any(i.startswith("review_assertion:table-checks:") for i in table["quality_issues"])


def test_unresolved_table_keeps_its_quality(tmp_path) -> None:
    document = _document()
    registry = _registry(tmp_path, document, verdict="unresolved", corrections=[])
    assert apply_table_checks(document, registry) == 0
    assert document["elements"][0]["quality_status"] == "needs_review"


def test_changed_cells_fail_instead_of_dropping_the_check(tmp_path) -> None:
    registry = _registry(tmp_path, _document())
    with pytest.raises(TableCheckError, match="cells changed"):
        apply_table_checks(_document("球墨铸铁井盖、雨水第"), registry)


def test_table_with_other_blockers_is_not_admitted(tmp_path) -> None:
    document = _document()
    document["elements"][0]["policy_reasons"].append("table_header_roles_unknown")
    registry = _registry(tmp_path, document)
    assert apply_table_checks(document, registry) == 0
    assert document["elements"][0]["quality_status"] == "needs_review"


def test_replacement_rebuilds_a_wrong_grid(tmp_path) -> None:
    document = _document()
    grid = {"row_count": 2, "column_count": 2, "cells": [
        {"row": 0, "column": 0, "column_span": 2, "text": "井盖种类"},
        {"row": 1, "column": 0, "text": "球墨铸铁井盖、雨水箅"},
        {"row": 1, "column": 1, "text": "GB/T 1348"}]}
    registry = _registry(tmp_path, document, corrections=[], replacement=grid,
                         method="agent_crop_check")
    assert apply_table_checks(document, registry) == 1
    content = document["elements"][0]["content"]
    assert (content["row_count"], content["column_count"]) == (2, 2)
    assert [c["text"] for c in content["cells"]] == ["井盖种类", "球墨铸铁井盖、雨水箅", "GB/T 1348"]


def test_replacement_must_cover_the_grid_once(tmp_path) -> None:
    document = _document()
    grid = {"row_count": 2, "column_count": 2, "cells": [
        {"row": 0, "column": 0, "column_span": 2, "text": "a"},
        {"row": 1, "column": 0, "text": "b"}]}
    with pytest.raises(TableCheckError, match="uncovered"):
        _registry(tmp_path, document, corrections=[], replacement=grid)
