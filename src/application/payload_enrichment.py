"""Shared metadata payload enrichment (T06 requirement 8).

The enrichment adds the evidence/document fields the retrieval + UI layer
expects but the raw V2 chunk payload does not carry. It is a CONTROLLED step:

* IDEMPOTENT -- applying the enrichment to an already-enriched payload
  produces the same values;
* it NEVER upgrades a ``usage_policy`` -- the chunker projection (strictest
  source wins, ``evidence_policy.chunk_usage_policy``) owns the policy. An
  existing policy is omitted from the enrichment so a ``set_payload``-style
  merge leaves it untouched; a MISSING policy is backfilled with the safest
  non-excluded default (``source_locator_only``), never ``answer_and_citation``.

Ingest applies this offline while building the point payloads.
"""

from __future__ import annotations

from typing import Any

# T06-R4: version of the enrichment step. Part of the stable build identity:
# changing the enrichment schema derives a NEW staging collection instead of
# silently reusing a collection built under the old schema.
ENRICHMENT_SCHEMA_VERSION = "t06.enrichment/1.0"


def build_enrichment(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    source_uri: str,
) -> dict[str, Any]:
    """Build the enrichment merge-dict for one chunk payload."""
    enrichment = {
        "primary_evidence_id": payload.get("chunk_id", ""),
        "document_version_id": (payload.get("source_sha256") or "").lower(),
        "file_name": metadata.get("file_name", ""),
        "document_kind": metadata.get("document_kind", ""),
        "jurisdictions": metadata.get("jurisdictions", []),
        "standard_number": metadata.get("standard_number", ""),
        "source_authority": metadata.get("source_authority", ""),
        "source_uri": source_uri,
        "publication_date": metadata.get("publication_date", ""),
        "effective_from": metadata.get("effective_from", ""),
        "effective_to": metadata.get("effective_to", ""),
        "effective_status": metadata.get("effective_status", "unknown"),
        "effective_status_as_of": metadata.get("effective_status_as_of", ""),
        "source_regions": [],
    }
    # Preserve an existing policy (omit -> no overwrite); backfill a safe
    # default only when missing.
    if not payload.get("usage_policy"):
        enrichment["usage_policy"] = "source_locator_only"
    return enrichment
