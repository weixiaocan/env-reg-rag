"""Build a formal corpus manifest only from explicitly approved source facts."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any


class FormalCorpusManifestBuilder:
    """Apply the formal source gate without changing any review decision."""

    _ACCEPTED_FILE_MATCHES = {"sha256_match", "content_equivalent_reviewed"}

    def project_chunks(
        self,
        *,
        review_rows: Sequence[dict[str, Any]],
        chunks: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        approved = {
            str(row["file_name"]): row
            for row in review_rows
            if row.get("source_review") == "official_fulltext_verified"
            and row.get("local_file_match") in self._ACCEPTED_FILE_MATCHES
            and row.get("effective_status") == "current"
            and row.get("selection_status") == "approved_formal"
        }
        projected = []
        for source_chunk in chunks:
            file_name = str(source_chunk.get("metadata", {}).get("file_name", ""))
            review = approved.get(file_name)
            if review is None:
                continue
            chunk = deepcopy(source_chunk)
            chunk["metadata"].update(
                {
                    "official_source_uri": review.get("official_source_uri", ""),
                    "source_authority": review.get("source_authority", ""),
                    "source_review": review.get("source_review", ""),
                    "publication_date": review.get("publication_date", ""),
                    "effective_status": review.get("effective_status", ""),
                    "effective_from": review.get("effective_date", ""),
                    "effective_to": "9999-12-31",
                    "local_file_match": review.get("local_file_match", ""),
                    "selection_status": review.get("selection_status", ""),
                }
            )
            projected.append(chunk)
        return projected

    def build(
        self,
        *,
        review_rows: Sequence[dict[str, Any]],
        chunks: Sequence[dict[str, Any]],
        corpus_version: str,
    ) -> dict[str, Any]:
        eligible_files: set[str] = set()
        excluded_documents = []

        for row in review_rows:
            reasons = []
            if row.get("source_review") != "official_fulltext_verified":
                reasons.append("official_fulltext_not_verified")
            if row.get("local_file_match") not in self._ACCEPTED_FILE_MATCHES:
                reasons.append("local_file_not_matched")
            if row.get("effective_status") != "current":
                reasons.append("effective_status_not_current")
            if row.get("selection_status") != "approved_formal":
                reasons.append("not_approved_for_formal_release")

            file_name = str(row["file_name"])
            if reasons:
                excluded_documents.append(
                    {"file_name": file_name, "reasons": reasons}
                )
            else:
                eligible_files.add(file_name)

        included_chunks = [
            chunk
            for chunk in chunks
            if chunk.get("metadata", {}).get("file_name") in eligible_files
        ]
        document_version_ids = list(
            dict.fromkeys(str(chunk["document_version_id"]) for chunk in included_chunks)
        )
        return {
            "corpus_version": corpus_version,
            "status": "ready" if included_chunks else "blocked",
            "document_version_ids": document_version_ids,
            "chunk_ids": [str(chunk["chunk_id"]) for chunk in included_chunks],
            "excluded_documents": excluded_documents,
        }
