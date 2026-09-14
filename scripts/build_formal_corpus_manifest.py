"""Build the formal corpus candidate manifest from reviewed source facts."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.formal_corpus import FormalCorpusManifestBuilder


REVIEWS_PATH = ROOT / "data" / "registry" / "document_reviews.csv"
CHUNKS_PATH = ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.jsonl"
OUTPUT_PATH = ROOT / "data" / "registry" / "formal-corpus-v1.json"
FORMAL_CHUNKS_PATH = ROOT / "data" / "retrieval" / "formal-corpus-v1-chunks.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    with REVIEWS_PATH.open(encoding="utf-8-sig", newline="") as handle:
        reviews = list(csv.DictReader(handle))
    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    builder = FormalCorpusManifestBuilder()
    manifest = builder.build(
        review_rows=reviews,
        chunks=chunks,
        corpus_version="formal-corpus-v1",
    )
    formal_chunks = builder.project_chunks(review_rows=reviews, chunks=chunks)
    if [chunk["chunk_id"] for chunk in formal_chunks] != manifest["chunk_ids"]:
        raise RuntimeError("formal chunk projection does not match the approved manifest")
    FORMAL_CHUNKS_PATH.write_text(
        "".join(
            json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in formal_chunks
        ),
        encoding="utf-8",
    )
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["inputs"] = {
        "document_reviews": REVIEWS_PATH.relative_to(ROOT).as_posix(),
        "document_reviews_sha256": _sha256(REVIEWS_PATH),
        "retrieval_chunks": CHUNKS_PATH.relative_to(ROOT).as_posix(),
        "retrieval_chunks_sha256": _sha256(CHUNKS_PATH),
        "formal_retrieval_chunks": FORMAL_CHUNKS_PATH.relative_to(ROOT).as_posix(),
        "formal_retrieval_chunks_sha256": _sha256(FORMAL_CHUNKS_PATH),
    }
    manifest["reviewed_document_count"] = len(reviews)
    manifest["included_document_count"] = len(manifest["document_version_ids"])
    manifest["included_chunk_count"] = len(manifest["chunk_ids"])
    OUTPUT_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "included_document_count": manifest["included_document_count"],
                "included_chunk_count": manifest["included_chunk_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
