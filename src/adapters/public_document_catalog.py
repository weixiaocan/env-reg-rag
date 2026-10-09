"""Read-only inventory catalog for public page browsing.

Browsing every local source file is deliberately separate from answer-corpus
admission.  Byte-identical aliases share one content-addressed document ID.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from src.adapters.inventory_document_catalog import DocumentSource
from src.application.pdf_source_audit import file_digest, registered_pdf_path


class PublicDocumentCatalog:
    def __init__(self, *, project_root: Path, inventory_path: Path) -> None:
        self._project_root = project_root.resolve()
        self._documents = self._load(inventory_path)

    def get(self, document_version_id: str) -> DocumentSource | None:
        return self._documents.get(document_version_id.lower())

    def list(self) -> list[DocumentSource]:
        return sorted(self._documents.values(), key=lambda item: item.file_name)

    def _load(self, inventory_path: Path) -> dict[str, DocumentSource]:
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                sha = (row.get("sha256") or "").strip().lower()
                if len(sha) == 64:
                    grouped[sha].append(row)

        documents: dict[str, DocumentSource] = {}
        for sha, rows in grouped.items():
            aliases = tuple(
                dict.fromkeys(
                    (row.get("file_name") or Path(row.get("rel_path") or "").name).strip()
                    for row in rows
                    if (row.get("file_name") or Path(row.get("rel_path") or "").name).strip()
                )
            )
            local_path: Path | None = None
            for row in rows:
                rel_path = (row.get("rel_path") or "").strip()
                if not rel_path:
                    continue
                try:
                    candidate = registered_pdf_path(self._project_root, rel_path)
                    if candidate.is_file() and file_digest(candidate) == sha:
                        local_path = candidate
                        break
                except (OSError, ValueError):
                    continue
            primary = rows[0]
            documents[sha] = DocumentSource(
                document_version_id=sha,
                file_name=(primary.get("file_name") or aliases[0] if aliases else sha),
                standard_no=(primary.get("std_no") or "").strip(),
                document_kind=(primary.get("document_kind") or "unknown").strip(),
                jurisdiction=(primary.get("jurisdiction") or "unknown").strip(),
                source_uri=(primary.get("official_source_uri") or "").strip(),
                local_path=local_path,
                aliases=aliases,
            )
        return documents
