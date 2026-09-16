"""Resolve the immutable corpus currently published for query traffic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CurrentCorpusArtifacts:
    corpus_version: str
    manifest_path: Path
    evidence_units_path: Path
    retrieval_chunks_path: Path
    collection_name: str
    query_alias: str = "corpus_current"


def resolve_current_corpus(project_root: Path) -> CurrentCorpusArtifacts:
    """Use the published pointer, with the original formal corpus as bootstrap."""

    root = Path(project_root).resolve()
    pointer_path = root / "data" / "registry" / "corpus-current.json"
    if not pointer_path.is_file():
        return CurrentCorpusArtifacts(
            corpus_version="formal-corpus-v1",
            manifest_path=root / "data" / "registry" / "formal-corpus-v1.json",
            evidence_units_path=(
                root / "data" / "evidence" / "m3-evidence-units-v1.jsonl"
            ),
            retrieval_chunks_path=(
                root / "data" / "retrieval" / "formal-corpus-v1-chunks.jsonl"
            ),
            collection_name="corpus_formal_v1",
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
    return CurrentCorpusArtifacts(
        corpus_version=pointer["corpus_version"],
        manifest_path=manifest_path,
        evidence_units_path=root / manifest["evidence_units"],
        retrieval_chunks_path=root / manifest["retrieval_chunks"],
        collection_name=pointer["collection_name"],
        query_alias=pointer.get("query_alias", "corpus_current"),
    )
