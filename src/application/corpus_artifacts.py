"""Resolve the current corpus manifest for OCR / processing tooling.

Returns the corpus version + manifest path + canonical documents jsonl path.
Formerly also resolved V1 evidence-unit / retrieval-chunk artifacts and a
``formal-corpus-v1`` bootstrap fallback; those V1 retrieval concerns were
removed when the V1 retrieval chain was deleted. The manifest written by
:mod:`src.application.corpus_update` now carries only the canonical documents
(page-intermediate OCR cache) plus page/structure status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CurrentCorpusArtifacts:
    corpus_version: str
    manifest_path: Path
    canonical_documents_path: Path
    # T06: the alias the runtime queries (None on pre-T06 pointers).
    alias: str | None = None
    # T06: the versioned collection the publish gate promoted. Recorded for
    # traceability -- NEVER used as the runtime query entry.
    collection_name: str | None = None


def resolve_current_corpus(project_root: Path) -> CurrentCorpusArtifacts:
    """Resolve the current corpus from the published pointer.

    Reads ``data/registry/corpus-current.json`` -> manifest, and returns the
    canonical documents jsonl path (the page-intermediate OCR cache consumed
    by V2 assembly). Raises ``ValueError`` if the pointer or manifest is
    missing or inconsistent -- there is no longer a V1 bootstrap fallback.
    """

    root = Path(project_root).resolve()
    pointer_path = root / "data" / "registry" / "corpus-current.json"
    if not pointer_path.is_file():
        raise ValueError(
            "data/registry/corpus-current.json not found; publish a corpus first"
        )

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("status") != "published":
        raise ValueError("current corpus pointer is not published")
    manifest_path = root / pointer["manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "ready":
        raise ValueError("current corpus manifest is not ready")
    if manifest.get("corpus_version") != pointer.get("corpus_version"):
        raise ValueError("current corpus pointer and manifest versions do not match")
    canonical_documents_path = root / manifest["canonical_documents"]
    return CurrentCorpusArtifacts(
        corpus_version=pointer["corpus_version"],
        manifest_path=manifest_path,
        canonical_documents_path=canonical_documents_path,
        alias=pointer.get("alias"),
        collection_name=pointer.get("collection_name"),
    )


def resolve_query_collection(
    project_root: Path, env_override: str | None = None
) -> str:
    """The single authoritative query entry (T06-R3 P1-2).

    The runtime ALWAYS queries the live alias: publish switches and
    rollbacks move the alias target on the server side, so a running
    service follows them without a restart. The pointer records the alias
    name and the concrete target, but the target is never the query entry.

    Resolution order: explicit env override (diagnostics) > the pointer's
    recorded alias > the legacy pointer's collection_name (pre-T06
    pointers, kept working until the next publish) > the default live
    alias.
    """
    if env_override:
        return env_override
    pointer_path = Path(project_root).resolve() / "data" / "registry" / "corpus-current.json"
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        if pointer.get("alias"):
            return str(pointer["alias"])
        if pointer.get("collection_name"):
            return str(pointer["collection_name"])  # legacy pre-T06 pointer
    return "corpus_v2_live"
