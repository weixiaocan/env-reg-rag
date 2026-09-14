"""Stable, citable evidence model derived from Canonical Documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceLocator:
    sample_ids: list[str]
    physical_pages: list[int]
    page_indices: list[int]
    element_ids: list[str]
    bboxes: list[list[float]] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceUnit:
    schema_version: str
    evidence_id: str
    evidence_type: str
    text: str
    normalized_text: str
    content_sha256: str
    asset_id: str
    document_version_id: str
    file_name: str
    source_uri: str
    processing_run_id: str
    quality_status: str
    usage_policy: str
    text_reliability: str
    title: str
    heading_path: list[str]
    context_scope: str
    locator: EvidenceLocator
    document_metadata: dict[str, str]
    table: dict[str, Any] | None = None

    def validate(self) -> None:
        if self.evidence_type not in {"clause", "paragraph", "table", "source_locator"}:
            raise ValueError("unsupported evidence type")
        if self.usage_policy not in {"answer_and_citation", "source_locator_only"}:
            raise ValueError("unsupported evidence usage policy")
        if self.usage_policy == "answer_and_citation" and self.quality_status != "approved":
            raise ValueError("answer evidence must come from an approved source page")
        if self.usage_policy == "source_locator_only" and self.evidence_type != "source_locator":
            raise ValueError("locator-only evidence must use the source_locator type")
        if self.evidence_type == "source_locator" and self.text_reliability == "quality_gate_approved":
            raise ValueError("source locators must not claim approved transcription")
        if not self.text.strip() or not self.normalized_text:
            raise ValueError("evidence text must not be empty")
        if not self.evidence_id.startswith("ev_") or len(self.evidence_id) != 35:
            raise ValueError("evidence_id must be an ev_ prefix plus 32 hex characters")
        if not self.locator.sample_ids or not self.locator.physical_pages:
            raise ValueError("evidence locator must identify a source page")
        if not self.locator.element_ids:
            raise ValueError("evidence locator must identify source elements")
        if self.evidence_type == "table" and not (self.table and self.table.get("cells")):
            raise ValueError("table evidence must preserve structured cells")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class RetrievalChunk:
    schema_version: str
    chunk_id: str
    text: str
    evidence_ids: list[str]
    primary_evidence_id: str
    document_version_id: str
    evidence_type: str
    physical_pages: list[int]
    metadata: dict[str, Any]

    def validate(self) -> None:
        if not self.chunk_id.startswith("chunk_") or len(self.chunk_id) != 38:
            raise ValueError("chunk_id must be a chunk_ prefix plus 32 hex characters")
        if not self.text.strip():
            raise ValueError("retrieval chunk text must not be empty")
        if not self.evidence_ids or self.primary_evidence_id not in self.evidence_ids:
            raise ValueError("retrieval chunk must reference its primary evidence")
        if not self.physical_pages:
            raise ValueError("retrieval chunk must preserve source pages")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
