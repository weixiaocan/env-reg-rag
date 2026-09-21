"""Allowlisted local PDF access derived from the source-evidence registry.

Replaces the V1 ``InventoryDocumentCatalog`` (which keyed on ``doc_<sha[:16]>``
from ``inventory.csv``). The V2 chunk payload carries the full 64-hex
``source_sha256``, so this catalog keys on the full sha256 and resolves the
local PDF path via ``source_evidence.jsonl`` ``aliases[0].rel_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.adapters.inventory_document_catalog import DocumentSource
from src.application.pdf_source_audit import registered_pdf_path


class SourceEvidenceDocumentCatalog:
    """Resolve full-sha256 document IDs to allowlisted local PDF paths."""

    def __init__(
        self,
        *,
        project_root: Path,
        registry_path: Path,
    ) -> None:
        self._project_root = project_root.resolve()
        self._documents = self._load(registry_path)

    def get(self, document_version_id: str) -> DocumentSource | None:
        return self._documents.get(document_version_id.lower())

    def _load(self, registry_path: Path) -> dict[str, DocumentSource]:
        documents: dict[str, DocumentSource] = {}
        if not registry_path.is_file():
            return documents
        with registry_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                sha = (record.get("file_sha256") or "").strip().lower()
                if len(sha) != 64:
                    continue
                aliases = record.get("aliases") or []
                if not aliases:
                    continue
                primary = aliases[0]
                rel_path = primary.get("rel_path") or ""
                file_name = primary.get("file_name") or ""
                local_path: Path | None = None
                if rel_path:
                    try:
                        candidate = registered_pdf_path(self._project_root, rel_path)
                        local_path = candidate if candidate.is_file() else None
                    except ValueError:
                        local_path = None
                documents[sha] = DocumentSource(
                    document_version_id=sha,
                    file_name=file_name,
                    standard_no="",
                    document_kind="unknown",
                    jurisdiction="unknown",
                    source_uri="",
                    local_path=local_path,
                )
        return documents
