"""Build search-optimized chunks that retain links to citable evidence units."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.evidence import RetrievalChunk


_CONTEXT_PROFILE = "evidence-context-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _artifact_reference(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _retrieval_text(unit: dict[str, Any]) -> str:
    metadata = unit["document_metadata"]
    parts = []
    if metadata.get("standard_number"):
        parts.append(f"标准号：{metadata['standard_number']}")
    parts.append(f"文档：{unit['file_name']}")
    if unit["heading_path"]:
        parts.append(f"标题路径：{' > '.join(unit['heading_path'])}")
    elif unit.get("title"):
        parts.append(f"标题：{unit['title']}")
    parts.append(f"原文：{unit['normalized_text']}")
    return "\n".join(parts)


def _chunk_id(unit: dict[str, Any], text: str) -> str:
    payload = {
        "context_profile": _CONTEXT_PROFILE,
        "evidence_ids": [unit["evidence_id"]],
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "chunk_" + hashlib.sha256(encoded).hexdigest()[:32]


def build_retrieval_chunks(
    project_root: Path,
    evidence_path: Path,
    output_dir: Path,
    *,
    artifact_prefix: str = "m3-retrieval-chunks-v1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build deterministic one-to-one retrieval chunks from evidence units."""

    root = Path(project_root).resolve()
    evidence_path = Path(evidence_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = output_dir / f"{artifact_prefix}.jsonl"
    manifest_path = output_dir / f"{artifact_prefix}.manifest.json"
    if (chunks_path.exists() or manifest_path.exists()) and not overwrite:
        raise FileExistsError("retrieval chunk output exists; use overwrite or a new prefix")

    evidence_units = _read_jsonl(evidence_path)
    chunks = []
    for unit in evidence_units:
        text = _retrieval_text(unit)
        chunk = RetrievalChunk(
            schema_version="1",
            chunk_id=_chunk_id(unit, text),
            text=text,
            evidence_ids=[unit["evidence_id"]],
            primary_evidence_id=unit["evidence_id"],
            document_version_id=unit["document_version_id"],
            evidence_type=unit["evidence_type"],
            physical_pages=unit["locator"]["physical_pages"],
            metadata={
                **unit["document_metadata"],
                "file_name": unit["file_name"],
                "source_uri": unit["source_uri"],
                "heading_path": unit["heading_path"],
                "context_scope": unit["context_scope"],
                "quality_status": unit["quality_status"],
                "usage_policy": unit["usage_policy"],
                "text_reliability": unit["text_reliability"],
                **({'source_regions': unit['source_regions']} if unit.get('source_regions') else {}),
            },
        )
        chunks.append(chunk.to_dict())

    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("duplicate retrieval chunk IDs generated")
    summary = {
        "source_evidence_count": len(evidence_units),
        "chunk_count": len(chunks),
        "evidence_type_counts": dict(
            sorted(Counter(chunk["evidence_type"] for chunk in chunks).items())
        ),
        "usage_policy_counts": dict(
            sorted(Counter(chunk["metadata"]["usage_policy"] for chunk in chunks).items())
        ),
    }
    with chunks_path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    manifest = {
        "artifact_id": artifact_prefix,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1",
        "scope": "approved_evidence_plus_quarantined_source_locators",
        "context_profile": _CONTEXT_PROFILE,
        "mapping": "one_retrieval_chunk_to_one_evidence_unit",
        "evidence_source": _artifact_reference(evidence_path, root),
        "evidence_sha256": _sha256(evidence_path),
        "summary": summary,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"chunks": chunks, "summary": summary, "manifest": manifest}
