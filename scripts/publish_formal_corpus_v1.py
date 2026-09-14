"""Build, verify, snapshot, and publish formal corpus v1 to Qdrant."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


MANIFEST_PATH = ROOT / "data" / "registry" / "formal-corpus-v1.json"
CHUNKS_PATH = ROOT / "data" / "retrieval" / "formal-corpus-v1-chunks.jsonl"
GOLDEN_PATH = ROOT / "data" / "evaluation" / "golden-set-v1.json"
OUTPUT_PATH = ROOT / "data" / "registry" / "formal-corpus-v1-release.json"
COLLECTION_NAME = "corpus_formal_v1"
QUERY_ALIAS = "corpus_current"
SMOKE_CASE_IDS = ("AI-007", "AI-008", "AI-009")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_smoke(
    *,
    index: QdrantRetrievalIndex,
    embedder: BgeSmallZhEmbedder,
    cases: list[dict[str, object]],
) -> list[dict[str, object]]:
    results = []
    for case in cases:
        question = str(case["question"])
        hits = index.search(
            HybridQuery(
                text=question,
                dense_vector=embedder.embed_query(question),
            ),
            mode="rrf",
            filters=case.get("required_filters") or None,
            limit=5,
        )
        returned_ids = [hit.primary_evidence_id for hit in hits]
        expected_ids = set(case.get("required_evidence_ids", [])) | set(
            case.get("acceptable_evidence_ids", [])
        )
        results.append(
            {
                "case_id": case["case_id"],
                "passed": bool(expected_ids.intersection(returned_ids)),
                "returned_evidence_ids": returned_ids,
            }
        )
    return results


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["status"] != "ready":
        raise RuntimeError("formal corpus manifest has not passed its source gate")
    if _sha256(CHUNKS_PATH) != manifest["inputs"]["formal_retrieval_chunks_sha256"]:
        raise RuntimeError("formal retrieval chunks do not match the manifest")

    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(chunks) != manifest["included_chunk_count"]:
        raise RuntimeError("formal retrieval chunk count does not match the manifest")

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))
    case_by_id = {case["case_id"]: case for case in golden["cases"]}
    smoke_cases = [case_by_id[case_id] for case_id in SMOKE_CASE_IDS]
    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    client = QdrantClient(url=qdrant_url, timeout=30)
    if client.collection_exists(COLLECTION_NAME):
        raise RuntimeError(
            f"immutable release collection already exists: {COLLECTION_NAME}"
        )

    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    release_index = QdrantRetrievalIndex(
        client=client,
        collection_name=COLLECTION_NAME,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    release_index.build(
        chunks,
        embedder.embed_documents([chunk["text"] for chunk in chunks]),
    )
    indexed_count = client.count(COLLECTION_NAME, exact=True).count
    if indexed_count != len(chunks):
        raise RuntimeError("Qdrant point count does not match formal chunks")

    pre_publish_smoke = _run_smoke(
        index=release_index,
        embedder=embedder,
        cases=smoke_cases,
    )
    if not all(bool(item["passed"]) for item in pre_publish_smoke):
        raise RuntimeError("pre-publish smoke test failed; alias was not changed")

    manager = QdrantCorpusReleaseManager(client=client)
    previous_alias_target = next(
        (
            item.collection_name
            for item in client.get_aliases().aliases
            if item.alias_name == QUERY_ALIAS
        ),
        None,
    )
    snapshot = manager.create_snapshot(collection_name=COLLECTION_NAME)
    manager.publish(collection_name=COLLECTION_NAME, alias_name=QUERY_ALIAS)
    current_index = QdrantRetrievalIndex(
        client=client,
        collection_name=QUERY_ALIAS,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    post_publish_smoke = _run_smoke(
        index=current_index,
        embedder=embedder,
        cases=smoke_cases,
    )
    if not all(bool(item["passed"]) for item in post_publish_smoke):
        if previous_alias_target is not None:
            manager.rollback(
                collection_name=previous_alias_target,
                alias_name=QUERY_ALIAS,
            )
        raise RuntimeError("post-publish smoke test failed")

    report = {
        "release_id": "formal-corpus-v1-release",
        "status": "published",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "corpus_version": manifest["corpus_version"],
        "collection_name": COLLECTION_NAME,
        "query_alias": QUERY_ALIAS,
        "previous_alias_target": previous_alias_target,
        "document_version_ids": manifest["document_version_ids"],
        "document_count": manifest["included_document_count"],
        "chunk_count": len(chunks),
        "manifest": MANIFEST_PATH.relative_to(ROOT).as_posix(),
        "manifest_sha256": _sha256(MANIFEST_PATH),
        "formal_chunks": CHUNKS_PATH.relative_to(ROOT).as_posix(),
        "formal_chunks_sha256": _sha256(CHUNKS_PATH),
        "snapshot": {
            "name": snapshot.snapshot_name,
            "checksum": snapshot.checksum,
        },
        "pre_publish_smoke": pre_publish_smoke,
        "post_publish_smoke": post_publish_smoke,
        "runtime": {
            "python": platform.python_version(),
            "qdrant_client": version("qdrant-client"),
            "qdrant_server": client.info().version,
            "qdrant_url": qdrant_url,
            "qdrant_mode": "self_hosted_docker",
        },
        "known_limitations": [
            "Formal corpus v1 contains only three approved documents.",
            "Numeric interval table retrieval remains a known M3 failure case.",
            "The QX/T 489-2019 document remains excluded until effective status is verified.",
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "document_count": report["document_count"],
                "chunk_count": report["chunk_count"],
                "collection_name": report["collection_name"],
                "query_alias": report["query_alias"],
                "pre_publish_smoke": report["pre_publish_smoke"],
                "post_publish_smoke": report["post_publish_smoke"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
