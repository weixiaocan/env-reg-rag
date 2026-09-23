"""V2 Canonical Document schema validator (plan 1.3).

Independent validation layer on top of the domain ``CanonicalDocument.from_dict``.
It performs two stages:

1. Structural / invariant / unknown-field / span+engine ID resolution via the
   domain model (``from_dict`` already encodes design §12 invariants and the
   closed-schema unknown-field rejection).
2. **Independent digest verification** (the core addition of 1.3): recompute the
   three V2 IDs from the model's canonical payloads and assert the stored values
   match both the required prefix and the SHA-256 hex.

The domain ``from_dict`` does not verify that the stored ``canonical_id`` /
``canonical_content_id`` / ``metadata_fingerprint`` are *correct* -- it only
offers the payload methods. This module is the second opinion / acceptance gate.
"""

from __future__ import annotations

import hashlib
from typing import Any

from src.domain.canonical_document import CanonicalDocument, SchemaError

# Canonical ID prefixes (design §4).
_PREFIX_CANONICAL = "canonical-sha256"
_PREFIX_CONTENT = "canonical-content-sha256"
_PREFIX_METADATA = "metadata-sha256"

# Mapping of stored field name -> expected prefix, for introspection / Assembly.
ID_PREFIXES: dict[str, str] = {
    "canonical_id": _PREFIX_CANONICAL,
    "canonical_content_id": _PREFIX_CONTENT,
    "metadata_fingerprint": _PREFIX_METADATA,
}


def _digest_hex(payload_bytes: bytes) -> str:
    """SHA-256 hex (lowercase, 64 chars) of a canonical payload byte string."""
    return hashlib.sha256(payload_bytes).hexdigest()


def _compute_ids_from_model(model: CanonicalDocument) -> dict[str, str]:
    """Recompute the three canonical IDs from a validated domain model."""
    return {
        "canonical_id": f"{_PREFIX_CANONICAL}:{_digest_hex(model.canonical_digest_payload())}",
        "canonical_content_id": f"{_PREFIX_CONTENT}:{_digest_hex(model.content_digest_payload())}",
        "metadata_fingerprint": f"{_PREFIX_METADATA}:{_digest_hex(model.metadata_digest_payload())}",
    }


def apply_computed_ids(doc: dict[str, Any], ids: dict[str, str]) -> None:
    """Write the three V2 digest IDs into `doc` from `compute_ids` output.

    Keep content and metadata fields distinct: assigning
    `metadata_fingerprint` into `canonical_content_id` fails validation
    (wrong prefix) and used to break whole-corpus assemble.
    """
    doc["canonical_id"] = ids["canonical_id"]
    doc["canonical_content_id"] = ids["canonical_content_id"]
    doc["metadata_fingerprint"] = ids["metadata_fingerprint"]


def compute_ids(data: dict[str, Any]) -> dict[str, str]:
    """Recompute the three V2 IDs from a raw document dict.

    Returns ``{"canonical_id", "canonical_content_id", "metadata_fingerprint"}``
    in their canonical prefixed form. Raises ``SchemaError`` if ``data`` fails
    structural / invariant validation (so the IDs are only meaningful for a
    structurally valid document). Intended for tests and the future Assembly
    (plan 1.5) to fill in correct IDs before emitting a document.
    """
    if not isinstance(data, dict):
        raise SchemaError("document must be a dict")
    model = CanonicalDocument.from_dict(data)
    return _compute_ids_from_model(model)


def _validate_hex(hexpart: str, where: str) -> None:
    if len(hexpart) != 64 or any(c not in "0123456789abcdef" for c in hexpart):
        raise SchemaError(f"{where}: expected 64 lowercase hex, got {hexpart!r}")


def _check_id(value: Any, expected_prefix: str, expected_hex: str, where: str) -> None:
    """Verify a stored ID has the right prefix and its hex equals the digest."""
    if not isinstance(value, str) or ":" not in value:
        raise SchemaError(
            f"{where}: malformed ID {value!r}, expected '{expected_prefix}:<hex>'"
        )
    prefix, _, hexpart = value.partition(":")
    if prefix != expected_prefix:
        raise SchemaError(
            f"{where}: expected prefix '{expected_prefix}:', got prefix {prefix!r}"
        )
    _validate_hex(hexpart, where)
    if hexpart != expected_hex:
        raise SchemaError(
            f"{where}: digest mismatch; expected {expected_hex}, got {hexpart}"
        )


def validate_document(data: dict[str, Any]) -> None:
    """Validate a V2 CanonicalDocument dict fully; raise ``SchemaError`` on any issue.

    Covers (plan 1.3):
      * type/content Schema strictness, 17 invariants, unknown-field rejection,
        span/engine ID resolvability -- delegated to ``CanonicalDocument.from_dict``.
      * independent digest verification of all three IDs (prefix + hex value).
    """
    if not isinstance(data, dict):
        raise SchemaError("document must be a dict")
    # Stage 1: structural + invariants + unknown-field + span/engine resolution.
    model = CanonicalDocument.from_dict(data)
    # Stage 2: independent digest verification (not performed by from_dict).
    expected = _compute_ids_from_model(model)
    _check_id(
        data["canonical_id"],
        _PREFIX_CANONICAL,
        expected["canonical_id"].split(":", 1)[1],
        "canonical_id",
    )
    _check_id(
        data["canonical_content_id"],
        _PREFIX_CONTENT,
        expected["canonical_content_id"].split(":", 1)[1],
        "canonical_content_id",
    )
    _check_id(
        data["metadata_fingerprint"],
        _PREFIX_METADATA,
        expected["metadata_fingerprint"].split(":", 1)[1],
        "metadata_fingerprint",
    )
