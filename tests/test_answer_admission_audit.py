from __future__ import annotations

from src.evaluation.answer_admission import build_admission_audit


def _element(
    element_id: str,
    *,
    kind: str = "text",
    quality: str | None = "passed",
    policy_reasons: list[str] | None = None,
) -> dict:
    element = {
        "element_id": element_id,
        "type": kind,
        "content": {"text": f"content-{element_id}"},
        "source_spans": [
            {
                "span_id": f"span-{element_id}",
                "role": "primary",
                "physical_page": 3,
                "bbox": [10.0, 20.0, 30.0, 40.0],
                "orientation_degrees": 0,
            }
        ],
        "provenance": [
            {"operation": "extract", "method": "native_text", "engine_id": "native"}
        ],
        "links": [],
        "policy_reasons": policy_reasons or [],
        "quality_issues": [],
    }
    if quality is not None:
        element["quality_status"] = quality
    return element


def _chunk(chunk_id: str, element_ids: list[str], policy: str, reasons: list[str]) -> dict:
    return {
        "chunk_id": chunk_id,
        "element_ids": element_ids,
        "source_spans": [{"physical_page": 3, "bbox": [10.0, 20.0, 30.0, 40.0]}],
        "metadata": {
            "usage_policy": policy,
            "quality_status": "passed" if policy == "answer_and_citation" else "needs_review",
            "policy_reasons": reasons,
        },
    }


def test_audit_deduplicates_source_objects_and_separates_strictest_policy_spread() -> None:
    document = {
        "canonical_id": "canonical-sha256:abc",
        "source": {
            "sha256": "a" * 64,
            "file_name": "fixture.pdf",
            "page_count": 3,
        },
        "elements": [
            _element("e-text"),
            _element("e-table", kind="table"),
            _element("e-missing", quality=None),
        ],
    }
    chunks = [
        _chunk("chunk-answer", ["e-text"], "answer_and_citation", []),
        _chunk(
            "chunk-mixed",
            ["e-text", "e-table"],
            "source_locator_only",
            ["missing_structured_review_assertion"],
        ),
        _chunk(
            "chunk-missing",
            ["e-missing"],
            "source_locator_only",
            ["missing_quality_status"],
        ),
    ]

    audit = build_admission_audit([document], chunks)

    assert audit["summary"] == {
        "chunk_count": 3,
        "answer_and_citation_chunks": 1,
        "source_locator_only_chunks": 2,
        "unique_source_object_count": 3,
        "locator_unique_source_object_count": 3,
        "intrinsic_locator_source_object_count": 2,
        "strictest_policy_spread_source_object_count": 1,
    }
    by_id = {row["element_id"]: row for row in audit["source_objects"]}
    assert by_id["e-table"]["root_cause"] == "default_conservative_policy"
    assert by_id["e-missing"]["root_cause"] == "missing_verifiable_quality_evidence"
    assert by_id["e-text"]["root_cause"] == "multi_source_strictest_policy_spread"
    assert by_id["e-text"]["affected_locator_chunk_ids"] == ["chunk-mixed"]
    assert by_id["e-text"]["affected_answer_chunk_ids"] == ["chunk-answer"]
    assert audit["chunks"][1]["source_object_keys"] == [
        f"{'a' * 64}:e-text",
        f"{'a' * 64}:e-table",
    ]


def test_audit_rejects_a_chunk_with_an_untraceable_element() -> None:
    document = {
        "canonical_id": "canonical-sha256:abc",
        "source": {"sha256": "b" * 64, "file_name": "fixture.pdf", "page_count": 1},
        "elements": [_element("e-known")],
    }
    chunks = [_chunk("chunk-bad", ["e-missing"], "source_locator_only", [])]

    try:
        build_admission_audit([document], chunks)
    except ValueError as exc:
        assert "untraceable element" in str(exc)
        assert "chunk-bad" in str(exc)
    else:
        raise AssertionError("untraceable chunk must fail closed")
