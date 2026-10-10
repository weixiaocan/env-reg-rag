from src.evaluation.text_duplication import (
    audit_chunk_repetitions,
    scan_adjacent_repetitions,
)


def test_adjacent_repetition_audit_uses_single_source_evidence() -> None:
    rows = [{
        "chunk_id": "c1", "pages": [1],
        "body": "甲段资产管理工作策略资产管理工作策略。乙段在系统之前，通之前，通过渗透。",
    }]
    report = audit_chunk_repetitions(
        rows, {1: ["资产管理工作策略资产管理工作策略", "在系统之前，通过渗透"]})
    assert report["count"] == 2
    assert report["source_present_legal_count"] == 1
    assert report["pipeline_introduced_count"] == 1


def test_adjacent_repetition_scanner_uses_required_cjk_rule() -> None:
    matches = scan_adjacent_repetitions("本文件。本文件。")
    assert matches == [{
        "start": 0, "end": 8, "fragment": "本文件。", "matched_text": "本文件。本文件。"}]
