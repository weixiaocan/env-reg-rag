#!/usr/bin/env python
"""One-shot enrichment of corpus_v2 Qdrant payloads.

The V2 chunk payload (V2Chunk.to_qdrant_payload) carries chunk text, source
metadata, and section_path, but is missing the evidence/document fields the
retrieval + UI layer expects: primary_evidence_id, document_version_id,
usage_policy, source_authority, source_uri, publication_date, effective_from,
effective_to, source_regions.

compute_chunk_id does NOT include metadata, so adding these fields cannot
change chunk_ids -- the existing vectors stay valid. This script enriches the
already-ingested points in place via ``set_payload`` (no re-embedding).

Idempotent: re-running overwrites the same fields with the same values.

Usage:
    PYTHONPATH=src python scripts/enrich_corpus_v2_payloads.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

CORPUS_VERSION = "corpus-37456321a968"
CANONICAL_DIR = _REPO_ROOT / "data" / "canonical" / "v2" / CORPUS_VERSION
SOURCE_EVIDENCE_PATH = _REPO_ROOT / "data" / "registry" / "source_evidence.jsonl"
COLLECTION = os.getenv("QDRANT_COLLECTION", "corpus_v2")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
BATCH_SIZE = 128


def _load_canonical_metadata() -> dict[str, dict]:
    """sha256 -> {document_kind, jurisdictions, effective_status, issuing_authorities, ...}."""
    out: dict[str, dict] = {}
    if not CANONICAL_DIR.is_dir():
        return out
    for path in sorted(CANONICAL_DIR.glob("*.canonical.json")):
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
        sha = (doc.get("source") or {}).get("sha256", "").lower()
        if len(sha) != 64:
            continue
        md = doc.get("metadata") or {}
        authorities = md.get("issuing_authorities") or []
        out[sha] = {
            "source_authority": authorities[0] if authorities else "",
            "publication_date": md.get("publication_date") or "",
            "effective_from": md.get("effective_from") or "",
            "effective_to": md.get("effective_to") or "",
        }
    return out


def _load_source_evidence_uris() -> dict[str, str]:
    """sha256 -> official_source_uri (from observations), empty if absent."""
    out: dict[str, str] = {}
    if not SOURCE_EVIDENCE_PATH.is_file():
        return out
    with SOURCE_EVIDENCE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            sha = (record.get("file_sha256") or "").lower()
            if len(sha) != 64:
                continue
            uri = ""
            for obs in record.get("observations") or []:
                if obs.get("field") == "official_source_uri":
                    uri = obs.get("value") or ""
                    if uri:
                        break
            out[sha] = uri
    return out


def main() -> int:
    from qdrant_client import QdrantClient, models

    canonical_md = _load_canonical_metadata()
    source_uris = _load_source_evidence_uris()
    print(f"[enrich] canonical metadata: {len(canonical_md)} docs")
    print(f"[enrich] source-evidence uris: {len(source_uris)} docs")

    client = QdrantClient(url=QDRANT_URL, timeout=60)
    if not client.collection_exists(COLLECTION):
        print(f"[enrich] ERROR: collection {COLLECTION!r} not found at {QDRANT_URL}")
        return 1

    info = client.get_collection(COLLECTION)
    total = info.points_count or 0
    print(f"[enrich] collection {COLLECTION}: {total} points")

    enriched = 0
    skipped = 0
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=COLLECTION,
            offset=offset,
            limit=BATCH_SIZE,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            break
        point_ids: list = []
        for record in records:
            payload = record.payload or {}
            chunk_id = payload.get("chunk_id", "")
            sha = (payload.get("source_sha256") or "").lower()
            if not chunk_id or len(sha) != 64:
                skipped += 1
                continue
            md = canonical_md.get(sha, {})
            enrichment = {
                "primary_evidence_id": chunk_id,
                "document_version_id": sha,
                "usage_policy": "answer_and_citation",
                "source_authority": md.get("source_authority", ""),
                "source_uri": source_uris.get(sha, ""),
                "publication_date": md.get("publication_date", ""),
                "effective_from": md.get("effective_from", ""),
                "effective_to": md.get("effective_to", ""),
                "source_regions": [],
            }
            client.set_payload(
                collection_name=COLLECTION,
                payload=enrichment,
                points=[record.id],
                wait=False,
            )
            point_ids.append(record.id)
            enriched += 1
        if point_ids:
            # ensure the last batch is flushed before scrolling on
            pass
        if offset is None:
            break

    print(f"[enrich] done: {enriched} enriched, {skipped} skipped (no chunk_id/sha)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
