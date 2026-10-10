from src.evaluation.clause_continuity import inspect_clause_continuity


def _clause(label: str, page: int = 1, text: str | None = None) -> dict:
    return {
        "element_id": f"e-{label}-{page}", "type": "text", "role": "clause",
        "label": label, "content": {"text": text or f"{label} 正文"},
        "source_spans": [{"physical_page": page}],
    }


def _text(text: str, page: int = 1, role: str = "paragraph") -> dict:
    return {
        "element_id": f"e-text-{page}-{text}", "type": "text", "role": role,
        "label": None, "content": {"text": text},
        "source_spans": [{"physical_page": page}],
    }


def test_clause_continuity_lists_gaps_duplicates_and_illegal_labels() -> None:
    report = inspect_clause_continuity({"elements": [
        _clause("3.4.1"), _clause("3.4.3"), _clause("3.4.3", 2), _clause("3.45"),
    ]})
    assert report["missing"] == [{
        "namespace": "body", "section": "3.4", "label": "3.4.2",
        "between_pages": [1, 1],
    }]
    assert report["duplicates"][0]["label"] == "3.4.3"
    assert report["illegal"][0]["label"] == "3.45"


def test_body_and_commentary_have_separate_clause_namespaces() -> None:
    report = inspect_clause_continuity({"elements": [
        _clause("1.0.1", 3), _clause("1.0.2", 3),
        _text("条文说明", 20),
        _clause("1.0.1", 21), _clause("1.0.2", 21),
    ]})

    assert report["duplicates"] == []
    assert report["summary"]["body_duplicate_count"] == 0
    assert report["summary"]["commentary_duplicate_count"] == 0


def test_formula_table_and_figure_numbers_are_excluded_from_clause_statistics() -> None:
    report = inspect_clause_continuity({"elements": [
        _clause("4.1.14"),
        _clause("4.1.15"),
        _clause("4.1.15", text="(4.1.15-4)"),
        _clause("4.1.16"),
        _clause("4.1.16", text="表4.1.16 设计参数"),
        _clause("4.1.17"),
        _clause("4.1.17", text="图4.1.17 流程图"),
        _clause("4.1.18"),
        _clause("4.1.18", text="式 4.1.18-2"),
        _clause("4.1.19"),
    ]})

    assert [row["label"] for row in report["excluded_non_clause_numbers"]] == [
        "4.1.15", "4.1.16", "4.1.17", "4.1.18",
    ]
    assert report["missing"] == []
    assert report["duplicates"] == []


def test_ocr_joined_trailing_digit_is_normalized_for_prose_clause() -> None:
    report = inspect_clause_continuity({"elements": [
        _clause("4.1.14"),
        _clause("4.1.154", text="4.1.154 综合生活污水量变化系数应按规定取值。"),
        _clause("4.1.16"),
    ]})

    assert report["missing"] == []
    assert report["duplicates"] == []
    assert report["corrected_ocr_joined_clause_labels"][0]["normalized_label"] == "4.1.15"
