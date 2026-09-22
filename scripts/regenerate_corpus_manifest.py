#!/usr/bin/env python
"""Regenerate the published corpus manifest from current V2 artifacts.

The manifest written by the pre-V2 corpus_update run still carried V1-era
retrieval fields (``evidence_unit_count``, ``chunk_ids`` with 5116 V1 ids,
``evidence_units`` / ``retrieval_chunks`` paths) pointing at files deleted
when the V1 chain was removed. This script rebuilds the manifest from the
artifacts that actually exist:

- page-intermediate canonical jsonl  -> document_version_ids, page counts,
  page_status_counts, canonical_documents_sha256
- structure audit json               -> structure_audit_sha256
- V2 ingest report                   -> Qdrant collection + chunk_count
- source_evidence registry           -> asset display names / metadata refresh

V1-era fields are dropped and replaced by a ``retrieval`` section describing
the Qdrant serving layer. ``corpus_version``, the pointer and canonical paths
are preserved: nothing is republished or re-ingested.

Usage::

    PYTHONPATH=src python scripts/regenerate_corpus_manifest.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from src.application.corpus_artifacts import resolve_current_corpus  # noqa: E402
from src.application.pdf_source_audit import (  # noqa: E402
    load_source_records,
    source_metadata,
)

# Fields from the V1-era manifest that no longer describe anything on disk.
STALE_FIELDS = (
    "evidence_unit_count",
    "chunk_ids",
    "evidence_units",
    "evidence_units_sha256",
    "retrieval_chunks",
    "retrieval_chunks_sha256",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_registry_metadata() -> dict[str, dict]:
    records = load_source_records(_REPO_ROOT)
    return {sha: source_metadata(record) for sha, record in records.items()}


def _refresh_asset(asset: dict, registry_meta: dict[str, dict]) -> list[str]:
    """Align one asset's display names / metadata with the current registry.

    Alias matching is per ``rel_path``: each ``source_files`` entry is renamed
    only by the registry alias registered for that exact path, so duplicate
    copies (e.g. ``_official_verification``) keep their own file names.
    """
    changed: list[str] = []
    sha = asset["sha256"]
    record = load_source_records(_REPO_ROOT).get(sha)
    md = registry_meta.get(sha, {})
    if record and record.get("aliases"):
        aliases = {a["rel_path"]: a for a in record["aliases"]}
        canonical = asset.get("canonical_rel_path", "")
        alias = aliases.get(canonical) or next(iter(aliases.values()))
        new_name = alias.get("file_name", "")
        if new_name and asset.get("display_file_name") != new_name:
            changed.append(f"display_file_name: {asset['display_file_name']!r} -> {new_name!r}")
            asset["display_file_name"] = new_name
        for source_file in asset.get("source_files") or []:
            entry = aliases.get(source_file.get("rel_path", ""))
            if entry and source_file.get("file_name") != entry.get("file_name"):
                source_file["file_name"] = entry["file_name"]
    for key in ("standard_number", "official_source_uri", "source_authority",
                "publication_date", "effective_from"):
        value = md.get(key)
        if value and asset.get("metadata", {}).get(key) != value:
            asset.setdefault("metadata", {})[key] = value
    eff = md.get("effective_status")
    if eff and eff != "unknown":
        if asset.get("metadata", {}).get("effective_status") != eff:
            asset["metadata"]["effective_status"] = eff
    return changed


def main() -> int:
    artifacts = resolve_current_corpus(_REPO_ROOT)
    pointer_path = _REPO_ROOT / "data" / "registry" / "corpus-current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_path = artifacts.manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry_meta = _load_registry_metadata()

    # Recompute facts from the page-intermediate canonical jsonl.
    version_ids: list[str] = []
    status_counts: dict[str, int] = {}
    page_total = 0
    with artifacts.canonical_documents_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            doc = json.loads(line)
            version_ids.append(doc["document_version_id"])
            for page in doc.get("pages") or []:
                page_total += 1
                status_counts[page.get("decision_status", "unknown")] = (
                    status_counts.get(page.get("decision_status", "unknown"), 0) + 1
                )

    removed: list[str] = []
    for field in STALE_FIELDS:
        if field in manifest:
            del manifest[field]
            removed.append(field)

    # Integrity: verify referenced artifact hashes against the files on disk.
    canonical_sha = _sha256_file(artifacts.canonical_documents_path)
    if manifest.get("canonical_documents_sha256") != canonical_sha:
        print(f"[manifest] canonical_documents_sha256 updated "
              f"({manifest.get('canonical_documents_sha256', '')[:12]} -> {canonical_sha[:12]})")
        manifest["canonical_documents_sha256"] = canonical_sha
    audit_rel = manifest.get("structure_audit")
    if audit_rel:
        audit_path = _REPO_ROOT / audit_rel
        if audit_path.is_file():
            audit_sha = _sha256_file(audit_path)
            if manifest.get("structure_audit_sha256") != audit_sha:
                manifest["structure_audit_sha256"] = audit_sha

    if manifest.get("document_version_ids") != version_ids:
        manifest["document_version_ids"] = version_ids
    if manifest.get("page_count") != page_total:
        manifest["page_count"] = page_total
    if manifest.get("page_status_counts") != status_counts:
        manifest["page_status_counts"] = dict(sorted(status_counts.items()))

    # V2 serving-layer facts replace the V1 retrieval fields.
    ingest_report_path = (
        _REPO_ROOT / "data" / "canonical" / "v2" / manifest["corpus_version"]
        / "ingest_report.json"
    )
    collection = pointer.get("collection_name", "corpus_v2")
    chunk_count = pointer.get("chunk_count")
    if ingest_report_path.is_file():
        report = json.loads(ingest_report_path.read_text(encoding="utf-8"))
        collection = report.get("collection", collection)
        chunk_count = report.get("total_chunks", chunk_count)
    manifest["retrieval"] = {
        "collection_name": collection,
        "chunk_count": chunk_count,
        "ingest_report": (
            ingest_report_path.relative_to(_REPO_ROOT).as_posix()
            if ingest_report_path.is_file() else None
        ),
    }

    for asset in manifest.get("assets") or []:
        for note in _refresh_asset(asset, registry_meta):
            print(f"[manifest] {asset['sha256'][:12]}: {note}")

    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()

    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False,
        dir=manifest_path.parent)
    try:
        handle.write(payload)
        handle.close()
        os.replace(handle.name, manifest_path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)
    print(f"[manifest] written: {manifest_path.relative_to(_REPO_ROOT)} "
          f"({len(removed)} stale fields removed: {', '.join(removed) or 'none'})")

    # Pointer: drop the rollback record if the file no longer exists.
    rollback = pointer.get("rollback_record")
    pointer_changed = False
    if rollback and not (_REPO_ROOT / rollback).is_file():
        del pointer["rollback_record"]
        pointer_changed = True
        print(f"[pointer] dropped missing rollback_record: {rollback}")
    if pointer_changed:
        pointer_payload = json.dumps(pointer, ensure_ascii=False, indent=2) + "\n"
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", delete=False, dir=pointer_path.parent)
        try:
            handle.write(pointer_payload)
            handle.close()
            os.replace(handle.name, pointer_path)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)

    # Final sanity: the pointer/manifest pair must still resolve.
    check = resolve_current_corpus(_REPO_ROOT)
    print(f"[manifest] resolve_current_corpus OK: {check.corpus_version} "
          f"-> {check.canonical_documents_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
