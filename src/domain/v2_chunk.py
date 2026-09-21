"""V2 Chunk domain model (Element-owned retrieval chunk).

Schema: ``v2.chunk/1.0``. See ``docs/v2-canonical-schema-design.md`` §9.3.

A V2 chunk is the retrieval-facing projection of a ``CanonicalDocument``: it is
cut from ``Element.content`` by the V2 Chunker (``src/application/v2_chunker``)
and carries a full provenance chain back to the source PDF::

    chunk -> canonical_id + element_ids -> Element.content -> source_spans
          -> PDF sha256 + physical_page + bbox

Unlike the V1 chunk (an evidence-unit 1:1 mirror carrying ``evidence_ids``), the
V2 chunk traces through ``canonical_id`` + ``element_ids`` + ``source_spans``
and uses ``canonical_content_id`` as its cache key so that a metadata-only
change never forces a re-chunk or a re-embedding (design §4.3).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.domain.canonical_document import SchemaError

ChunkSchemaVersion = Literal["v2.chunk/1.0"]
CHUNK_SCHEMA_VERSION: ChunkSchemaVersion = "v2.chunk/1.0"

DEFAULT_PROJECTION_VERSION = "chunk-text/v1"


# --------------------------------------------------------------------------- #
# Helpers (mirror the canonical_document idiom for consistency)
# --------------------------------------------------------------------------- #

def _require_keys(data: dict[str, Any], keys: set[str], where: str) -> None:
    missing = sorted(keys - set(data))
    if missing:
        raise SchemaError(f"{where}: missing required keys {missing}")
    unknown = sorted(set(data) - keys)
    if unknown:
        raise SchemaError(f"{where}: unknown keys {unknown}")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_chunk_id(
    *,
    canonical_content_id: str,
    element_ids: list[str],
    projection_version: str,
    text: str,
) -> str:
    """Deterministic chunk id (design §9.3).

    ``chunk_`` + the first 32 hex of ``sha256(canonical_content_id +
    element_ids + projection_version + text_sha256)``. ``canonical_content_id``
    is the cache key: a metadata-only change leaves it (and therefore the chunk
    id and its embedding) untouched.
    """
    payload = {
        "canonical_content_id": canonical_content_id,
        "element_ids": list(element_ids),
        "projection_version": projection_version,
        "text_sha256": _text_sha256(text),
    }
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return "chunk_" + digest[:32]


# --------------------------------------------------------------------------- #
# Source-span projection (page + bbox only, deduplicated)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ChunkSourceSpan:
    physical_page: int
    bbox: list[float]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChunkSourceSpan":
        _require_keys(data, {"physical_page", "bbox"}, "source_spans[]")
        bbox = list(data["bbox"])
        if len(bbox) != 4:
            raise SchemaError("source_spans[].bbox must have 4 floats")
        x0, y0, x1, y1 = bbox
        if not (x0 <= x1 and y0 <= y1):
            raise SchemaError("source_spans[].bbox must satisfy x0<=x1 and y0<=y1")
        page = data["physical_page"]
        if not isinstance(page, int) or page <= 0:
            raise SchemaError("source_spans[].physical_page must be a positive int")
        return cls(physical_page=page, bbox=bbox)


# --------------------------------------------------------------------------- #
# V2Chunk
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class V2Chunk:
    schema_version: ChunkSchemaVersion
    chunk_id: str
    canonical_id: str
    canonical_content_id: str
    element_ids: list[str]
    text: str
    source_spans: list[ChunkSourceSpan]
    projection_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "V2Chunk":
        _require_keys(
            data,
            {
                "schema_version", "chunk_id", "canonical_id", "canonical_content_id",
                "element_ids", "text", "source_spans", "projection_version", "metadata",
            },
            "chunk",
        )
        if data["schema_version"] != CHUNK_SCHEMA_VERSION:
            raise SchemaError(
                f"schema_version must be {CHUNK_SCHEMA_VERSION!r}, got {data['schema_version']!r}"
            )
        chunk = cls(
            schema_version=data["schema_version"],
            chunk_id=data["chunk_id"],
            canonical_id=data["canonical_id"],
            canonical_content_id=data["canonical_content_id"],
            element_ids=list(data["element_ids"]),
            text=data["text"],
            source_spans=[ChunkSourceSpan.from_dict(s) for s in data["source_spans"]],
            projection_version=data["projection_version"],
            metadata=dict(data["metadata"]),
        )
        chunk.validate()
        return chunk

    def validate(self) -> None:
        # chunk_id format: "chunk_" + 32 lowercase hex.
        cid = self.chunk_id
        if not isinstance(cid, str) or not cid.startswith("chunk_"):
            raise SchemaError(f"chunk_id must start with 'chunk_', got {cid!r}")
        hexpart = cid[len("chunk_"):]
        if len(hexpart) != 32 or any(c not in "0123456789abcdef" for c in hexpart):
            raise SchemaError(f"chunk_id must be 'chunk_' + 32 hex, got {cid!r}")
        # text non-empty.
        if not isinstance(self.text, str) or not self.text.strip():
            raise SchemaError("chunk.text must be a non-empty string")
        # element_ids non-empty + unique.
        if not self.element_ids:
            raise SchemaError("chunk.element_ids must be non-empty")
        if len(self.element_ids) != len(set(self.element_ids)):
            raise SchemaError("chunk.element_ids must be unique")
        # source_spans non-empty.
        if not self.source_spans:
            raise SchemaError("chunk.source_spans must be non-empty")
        # projection_version non-empty.
        if not self.projection_version:
            raise SchemaError("chunk.projection_version must be non-empty")
        # metadata is a plain dict (closed at the chunker side, not here).

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "chunk_id": self.chunk_id,
            "canonical_id": self.canonical_id,
            "canonical_content_id": self.canonical_content_id,
            "element_ids": list(self.element_ids),
            "text": self.text,
            "source_spans": [asdict(s) for s in self.source_spans],
            "projection_version": self.projection_version,
            "metadata": dict(self.metadata),
        }

    def to_qdrant_payload(self) -> dict[str, Any]:
        """Flatten into the dict shape ``QdrantRetrievalIndex.build`` expects.

        ``build`` stores ``{**chunk, **chunk.get("metadata", {})}`` as the point
        payload, so we fold ``metadata`` into the top level (no nested
        ``metadata`` key) and keep ``chunk_id`` / ``text`` at the top level --
        this is wire-compatible with the V1 chunk dict without touching
        ``QdrantRetrievalIndex``.
        """
        payload = {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "schema_version": self.schema_version,
            "canonical_id": self.canonical_id,
            "canonical_content_id": self.canonical_content_id,
            "element_ids": list(self.element_ids),
            "source_spans": [asdict(s) for s in self.source_spans],
            "projection_version": self.projection_version,
        }
        payload.update(self.metadata)
        return payload
