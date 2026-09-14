"""Project-owned canonical document model independent of parser frameworks."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CanonicalElement:
    element_id: str
    type: str
    text: str
    normalized_text: str
    page_index: int
    bbox: list[float] | None
    coordinate_origin: str = "top_left"
    reading_order: int = 0
    heading_path: list[str] = field(default_factory=list)
    clause_path: list[str] = field(default_factory=list)
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(frozen=True)
class CanonicalTableCell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    bbox: list[float] | None = None


@dataclass(frozen=True)
class CanonicalTable:
    element_id: str
    caption: str
    html: str
    markdown: str
    cells: list[CanonicalTableCell]
    unit_context: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalPage:
    sample_id: str
    physical_page: int
    page_index: int
    display_page_label: str | None
    width: float
    height: float
    rotation: int
    extraction_route: str
    parser_name: str
    parser_profile: str
    decision_status: str
    decision_reasons: list[str]
    publishable: bool
    text: str
    elements: list[CanonicalElement]
    tables: list[CanonicalTable]
    raw_artifact_ref: str
    coordinate_normalizations: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.publishable != (self.decision_status == "approved"):
            raise ValueError("publishable must be true only for approved pages")
        if self.physical_page != self.page_index + 1:
            raise ValueError("physical_page must equal page_index + 1")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")
        for element in self.elements:
            if element.page_index != self.page_index:
                raise ValueError("element page_index must match its page")
            if element.bbox is not None:
                x0, y0, x1, y1 = element.bbox
                if not (0 <= x0 <= x1 <= self.width and 0 <= y0 <= y1 <= self.height):
                    raise ValueError(f"element bbox is outside page: {element.element_id}")


@dataclass(frozen=True)
class CanonicalDocument:
    schema_version: str
    scope: str
    asset_id: str
    document_version_id: str
    file_name: str
    sha256: str
    source_uri: str
    processing_run_id: str
    config_hash: str
    pages: list[CanonicalPage]

    def validate(self) -> None:
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("canonical document sha256 must be 64 hexadecimal characters")
        sample_ids = [page.sample_id for page in self.pages]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("canonical document sample_ids must be unique")
        for page in self.pages:
            page.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
