from src.evaluation.clause_continuity import inspect_clause_continuity


def _clause(label: str, page: int = 1) -> dict:
    return {
        "element_id": f"e-{label}-{page}", "type": "text", "role": "clause",
        "label": label, "content": {"text": f"{label} 正文"},
        "source_spans": [{"physical_page": page}],
    }


def test_clause_continuity_lists_gaps_duplicates_and_illegal_labels() -> None:
    report = inspect_clause_continuity({"elements": [
        _clause("3.4.1"), _clause("3.4.3"), _clause("3.4.3", 2), _clause("3.45"),
    ]})
    assert report["missing"] == [{"section": "3.4", "label": "3.4.2"}]
    assert report["duplicates"][0]["label"] == "3.4.3"
    assert report["illegal"][0]["label"] == "3.45"
