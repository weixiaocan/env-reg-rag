"""Allowlisted local PDF access derived from the corpus inventory."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DocumentSource:
    document_version_id: str
    file_name: str
    standard_no: str
    document_kind: str
    jurisdiction: str
    source_uri: str
    local_path: Path | None


class DocumentCatalog(Protocol):
    """Structural interface for document-version -> local-PDF resolution."""

    def get(self, document_version_id: str) -> DocumentSource | None: ...


class InventoryDocumentCatalog:
    """Resolve document IDs without exposing arbitrary filesystem paths."""

    def __init__(self, *, project_root: Path, inventory_path: Path) -> None:
        self._project_root = project_root.resolve()
        self._raw_root = (self._project_root / "data" / "raw").resolve()
        self._documents = self._load(inventory_path)

    def get(self, document_version_id: str) -> DocumentSource | None:
        return self._documents.get(document_version_id)

    def _load(self, inventory_path: Path) -> dict[str, DocumentSource]:
        documents: dict[str, DocumentSource] = {}
        full_hashes: dict[str, str] = {}
        with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                sha256 = (row.get("sha256") or "").strip().lower()
                if len(sha256) != 64:
                    continue
                document_id = f"doc_{sha256[:16]}"
                if document_id in documents:
                    if full_hashes[document_id] == sha256:
                        continue
                    raise ValueError(f"document version prefix collision: {document_id}")
                candidate = (self._project_root / (row.get("rel_path") or "")).resolve()
                local_path = (
                    candidate
                    if candidate.suffix.lower() == ".pdf"
                    and candidate.is_relative_to(self._raw_root)
                    and candidate.is_file()
                    else None
                )
                documents[document_id] = DocumentSource(
                    document_version_id=document_id,
                    file_name=(row.get("file_name") or "").strip(),
                    standard_no=(row.get("std_no") or "").strip(),
                    document_kind=(row.get("document_kind") or "unknown").strip(),
                    jurisdiction=(row.get("jurisdiction") or "unknown").strip(),
                    source_uri=(row.get("official_source_uri") or "").strip(),
                    local_path=local_path,
                )
                full_hashes[document_id] = sha256
        return documents
