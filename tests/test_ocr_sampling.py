from __future__ import annotations

from types import SimpleNamespace

from src.evaluation.ocr_sampling import (
    chunk_exclusion_reasons,
    sample_record,
    select_document_samples,
    split_sampling_population,
    union_bboxes_by_page,
)


def _chunk(
    index: int,
    page: int,
    *,
    reasons: list[str] | None = None,
    element_types: list[str] | None = None,
    spans: list[tuple[int, list[float]]] | None = None,
):
    return SimpleNamespace(
        chunk_id=f"chunk_{index:032x}",
        text=f"text {index}",
        metadata={
            "usage_policy": "source_locator_only",
            "policy_reasons": reasons or ["ocr_source_review", "needs_review_source"],
            "element_types": element_types or ["text"],
        },
        source_spans=[
            SimpleNamespace(physical_page=p, bbox=bbox)
            for p, bbox in (spans or [(page, [1.0, 2.0, 3.0, 4.0])])
        ],
    )


def test_sampling_is_deterministic_and_covers_front_middle_back():
    chunks = [_chunk(index, index + 1) for index in range(90)]
    first = select_document_samples(chunks, "a" * 64)
    second = select_document_samples(list(reversed(chunks)), "a" * 64)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert len(first) == 25
    pages = [chunk.source_spans[0].physical_page for chunk in first]
    assert any(page <= 30 for page in pages)
    assert any(31 <= page <= 60 for page in pages)
    assert any(page >= 61 for page in pages)


def test_sampling_population_accepts_only_pure_ocr_source_text():
    accepted = _chunk(1, 1, reasons=["ocr_source_review", "needs_review_source", "too_short"])
    structured = _chunk(2, 2, element_types=["text", "table"])
    quarantined = _chunk(3, 3, reasons=["ocr_source_review", "quarantined_source"])
    mojibake = _chunk(4, 4, reasons=["needs_review_source", "mojibake_symbol_text"])
    eligible, excluded = split_sampling_population(
        [accepted, structured, quarantined, mojibake]
    )
    assert eligible == [accepted]
    assert excluded["contains_table_element"] == 1
    assert excluded["quarantined_source"] == 1
    assert excluded["mojibake_symbol_text"] == 1
    assert chunk_exclusion_reasons(accepted) == []


def test_union_bboxes_by_page():
    spans = [
        SimpleNamespace(physical_page=2, bbox=[10, 20, 30, 40]),
        SimpleNamespace(physical_page=2, bbox=[5, 25, 35, 38]),
        SimpleNamespace(physical_page=3, bbox=[1, 2, 3, 4]),
    ]
    assert union_bboxes_by_page(spans) == [
        {"physical_page": 2, "bbox": [5.0, 20.0, 35.0, 40.0]},
        {"physical_page": 3, "bbox": [1.0, 2.0, 3.0, 4.0]},
    ]


def test_cross_page_record_has_one_crop_per_page():
    chunk = _chunk(
        1,
        4,
        spans=[(4, [1, 2, 3, 4]), (5, [5, 6, 7, 8]), (5, [4, 7, 9, 10])],
    )
    record = sample_record(
        chunk,
        document_name="scan.pdf",
        document_sha256="b" * 64,
        sample_id="ocr-bbbbbbbbbbbb-001",
    )
    assert record["physical_pages"] == [4, 5]
    assert [row["physical_page"] for row in record["screenshots"]] == [4, 5]
    assert record["page_bboxes"][1]["bbox"] == [4.0, 6.0, 9.0, 10.0]


def test_population_smaller_than_25_is_all_selected_in_stable_order():
    chunks = [_chunk(3, 9), _chunk(1, 2), _chunk(2, 5)]
    selected = select_document_samples(chunks, "c" * 64)
    assert [chunk.chunk_id for chunk in selected] == [
        chunks[1].chunk_id,
        chunks[2].chunk_id,
        chunks[0].chunk_id,
    ]
