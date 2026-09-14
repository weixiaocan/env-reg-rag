"""Rehearse publish, snapshot restore, alias rebuild, and rollback on M3 experiment data."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from qdrant_client import QdrantClient, models


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


CHUNKS_PATH = ROOT / "data" / "retrieval" / "m3-retrieval-chunks-v1.jsonl"
GOLDEN_PATH = ROOT / "data" / "evaluation" / "golden-set-v1.json"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m3-qdrant-release-recovery-v1.json"
SOURCE_COLLECTION = "m3_experiment_release_v1"
RESTORED_COLLECTION = "m3_experiment_restored_v1"
QUERY_ALIAS = "m3_experiment_current"
SMOKE_CASE_ID = "AI-010"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_alias_if_present(client: QdrantClient, alias_name: str) -> None:
    if alias_name in {item.alias_name for item in client.get_aliases().aliases}:
        client.update_collection_aliases(
            [
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias_name)
                )
            ]
        )


def _smoke(
    *,
    client: QdrantClient,
    embedder: BgeSmallZhEmbedder,
    question: str,
    expected_evidence_ids: set[str],
) -> dict[str, object]:
    current = QdrantRetrievalIndex(
        client=client,
        collection_name=QUERY_ALIAS,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    hits = current.search(
        HybridQuery(text=question, dense_vector=embedder.embed_query(question)),
        mode="rrf",
        limit=5,
    )
    evidence_ids = [hit.primary_evidence_id for hit in hits]
    return {
        "passed": bool(expected_evidence_ids.intersection(evidence_ids)),
        "returned_evidence_ids": evidence_ids,
    }


def main() -> None:
    chunks = [
        json.loads(line)
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))
    smoke_case = next(case for case in golden["cases"] if case["case_id"] == SMOKE_CASE_ID)
    expected_ids = set(smoke_case["required_evidence_ids"]) | set(
        smoke_case.get("acceptable_evidence_ids", [])
    )

    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    client = QdrantClient(url=qdrant_url, timeout=30)
    embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
    manager = QdrantCorpusReleaseManager(client=client)

    _remove_alias_if_present(client, QUERY_ALIAS)
    for collection_name in (SOURCE_COLLECTION, RESTORED_COLLECTION):
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)

    source = QdrantRetrievalIndex(
        client=client,
        collection_name=SOURCE_COLLECTION,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    source.build(chunks, embedder.embed_documents([chunk["text"] for chunk in chunks]))
    manager.publish(collection_name=SOURCE_COLLECTION, alias_name=QUERY_ALIAS)
    source_smoke = _smoke(
        client=client,
        embedder=embedder,
        question=smoke_case["question"],
        expected_evidence_ids=expected_ids,
    )

    snapshot = manager.create_snapshot(collection_name=SOURCE_COLLECTION)
    manager.restore_snapshot(
        snapshot=snapshot,
        restored_collection_name=RESTORED_COLLECTION,
    )
    manager.publish(collection_name=RESTORED_COLLECTION, alias_name=QUERY_ALIAS)
    restored_smoke = _smoke(
        client=client,
        embedder=embedder,
        question=smoke_case["question"],
        expected_evidence_ids=expected_ids,
    )

    manager.rollback(collection_name=SOURCE_COLLECTION, alias_name=QUERY_ALIAS)
    rollback_smoke = _smoke(
        client=client,
        embedder=embedder,
        question=smoke_case["question"],
        expected_evidence_ids=expected_ids,
    )
    passed = all(
        bool(item["passed"])
        for item in (source_smoke, restored_smoke, rollback_smoke)
    )
    report = {
        "artifact_id": "m3-qdrant-release-recovery-v1",
        "status": "passed" if passed else "failed",
        "scope": "experiment_only_not_formal_corpus",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "chunks": CHUNKS_PATH.relative_to(ROOT).as_posix(),
            "chunks_sha256": _sha256(CHUNKS_PATH),
            "chunk_count": len(chunks),
            "golden_set": GOLDEN_PATH.relative_to(ROOT).as_posix(),
            "golden_set_sha256": _sha256(GOLDEN_PATH),
        },
        "release": {
            "source_collection": SOURCE_COLLECTION,
            "restored_collection": RESTORED_COLLECTION,
            "query_alias": QUERY_ALIAS,
            "snapshot_name": snapshot.snapshot_name,
            "snapshot_checksum": snapshot.checksum,
            "final_alias_target": SOURCE_COLLECTION,
            "note": "Qdrant snapshots do not restore aliases; the release manager rebuilt the alias explicitly.",
        },
        "smoke_case": {
            "case_id": SMOKE_CASE_ID,
            "expected_evidence_ids": sorted(expected_ids),
            "after_source_publish": source_smoke,
            "after_snapshot_restore_publish": restored_smoke,
            "after_rollback": rollback_smoke,
        },
        "runtime": {
            "python": platform.python_version(),
            "qdrant_client": version("qdrant-client"),
            "qdrant_server": client.info().version,
            "qdrant_url": qdrant_url,
            "qdrant_mode": "self_hosted_docker",
        },
        "limitations": [
            "The current 58 chunks come from experiment-only samples and are not formal corpus v1.",
            "This is a local single-node recovery rehearsal, not a remote disaster-recovery drill.",
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "smoke_case": report["smoke_case"]}, ensure_ascii=False, indent=2))
    print(OUTPUT_PATH)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
