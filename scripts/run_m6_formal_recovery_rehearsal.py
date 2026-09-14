"""Back up formal Qdrant data outside its volume, restore, publish, and roll back."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from qdrant_client import QdrantClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.retrieval.bge_small_zh import BgeSmallZhEmbedder
from src.retrieval.qdrant_index import HybridQuery, QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
APP_URL = "http://127.0.0.1:8000"
SOURCE_COLLECTION = "corpus_formal_v1"
RESTORED_COLLECTION = "corpus_formal_v1_m6_restored"
QUERY_ALIAS = "corpus_current"
BACKUP_DIR = ROOT / "data" / "backups" / "qdrant"
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m6-formal-recovery-v1.json"
SMOKE_QUESTION = "重庆导则的智慧感知建设加分项要求开展哪些监测工作？"
EXPECTED_EVIDENCE_ID = "ev_6de4def06fa0cc2273e5fc2b5761ff79"


def alias_target(client: QdrantClient) -> str | None:
    return next(
        (
            item.collection_name
            for item in client.get_aliases().aliases
            if item.alias_name == QUERY_ALIAS
        ),
        None,
    )


def collection_fingerprint(client: QdrantClient, collection_name: str) -> str:
    points = []
    offset = None
    while True:
        page, offset = client.scroll(
            collection_name=collection_name,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        points.extend(point.model_dump(mode="json") for point in page)
        if offset is None:
            break
    canonical = json.dumps(
        sorted(points, key=lambda item: str(item["id"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def retrieval_smoke(
    *, client: QdrantClient, embedder: BgeSmallZhEmbedder
) -> dict[str, object]:
    index = QdrantRetrievalIndex(
        client=client,
        collection_name=QUERY_ALIAS,
        vector_size=embedder.dimension,
        enable_bm25=True,
    )
    hits = index.search(
        HybridQuery(
            text=SMOKE_QUESTION,
            dense_vector=embedder.embed_query(SMOKE_QUESTION),
        ),
        mode="rrf",
        filters={"jurisdiction": "重庆"},
        limit=5,
    )
    evidence_ids = [hit.primary_evidence_id for hit in hits]
    return {
        "passed": EXPECTED_EVIDENCE_ID in evidence_ids,
        "expected_evidence_id": EXPECTED_EVIDENCE_ID,
        "returned_evidence_ids": evidence_ids,
    }


def app_readiness() -> dict:
    with httpx.Client(base_url=APP_URL, timeout=10, trust_env=False) as client:
        response = client.get("/api/v1/ready")
        response.raise_for_status()
        return response.json()


def download_snapshot(snapshot_name: str) -> tuple[Path, str, int]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    destination = BACKUP_DIR / snapshot_name
    with httpx.Client(base_url=QDRANT_URL, timeout=60, trust_env=False) as client:
        response = client.get(
            f"/collections/{SOURCE_COLLECTION}/snapshots/{snapshot_name}"
        )
        response.raise_for_status()
        content = response.content
    destination.write_bytes(content)
    return destination, hashlib.sha256(content).hexdigest(), len(content)


def restore_uploaded_snapshot(snapshot_path: Path) -> None:
    with httpx.Client(base_url=QDRANT_URL, timeout=120, trust_env=False) as client:
        with snapshot_path.open("rb") as stream:
            response = client.post(
                f"/collections/{RESTORED_COLLECTION}/snapshots/upload",
                params={"wait": "true"},
                files={
                    "snapshot": (
                        snapshot_path.name,
                        stream,
                        "application/octet-stream",
                    )
                },
            )
        response.raise_for_status()


def main() -> None:
    client = QdrantClient(url=QDRANT_URL, timeout=30)
    if not client.collection_exists(SOURCE_COLLECTION):
        raise RuntimeError("formal source collection is missing")
    if alias_target(client) != SOURCE_COLLECTION:
        raise RuntimeError("formal query alias is not on the approved source release")

    manager = QdrantCorpusReleaseManager(client=client)
    if client.collection_exists(RESTORED_COLLECTION):
        client.delete_collection(RESTORED_COLLECTION)

    source_count = client.count(SOURCE_COLLECTION, exact=True).count
    source_fingerprint = collection_fingerprint(client, SOURCE_COLLECTION)
    snapshot = manager.create_snapshot(collection_name=SOURCE_COLLECTION)
    backup_path, backup_sha256, backup_bytes = download_snapshot(
        snapshot.snapshot_name
    )
    if snapshot.checksum and backup_sha256 != snapshot.checksum:
        raise RuntimeError("downloaded backup checksum does not match Qdrant snapshot")

    restored_smoke = None
    rollback_smoke = None
    restored_fingerprint = None
    readiness_after_publish = None
    readiness_after_rollback = None
    try:
        restore_uploaded_snapshot(backup_path)
        restored_count = client.count(RESTORED_COLLECTION, exact=True).count
        restored_fingerprint = collection_fingerprint(client, RESTORED_COLLECTION)

        embedder = BgeSmallZhEmbedder(local_files_only=True, device="cpu")
        manager.publish(
            collection_name=RESTORED_COLLECTION,
            alias_name=QUERY_ALIAS,
        )
        readiness_after_publish = app_readiness()
        restored_smoke = retrieval_smoke(client=client, embedder=embedder)

        # Withdrawing the recovered candidate means atomically returning the
        # stable alias to the previous approved immutable release.
        manager.rollback(
            collection_name=SOURCE_COLLECTION,
            alias_name=QUERY_ALIAS,
        )
        readiness_after_rollback = app_readiness()
        rollback_smoke = retrieval_smoke(client=client, embedder=embedder)
    finally:
        if alias_target(client) != SOURCE_COLLECTION:
            manager.rollback(
                collection_name=SOURCE_COLLECTION,
                alias_name=QUERY_ALIAS,
            )
        if client.collection_exists(RESTORED_COLLECTION):
            client.delete_collection(RESTORED_COLLECTION)

    checks = {
        "external_backup_checksum_matches": (
            snapshot.checksum is None or backup_sha256 == snapshot.checksum
        ),
        "restored_point_count_matches": restored_count == source_count == 29,
        "restored_fingerprint_matches": restored_fingerprint == source_fingerprint,
        "restored_release_searchable": bool(restored_smoke and restored_smoke["passed"]),
        "readiness_after_publish": (
            readiness_after_publish is not None
            and readiness_after_publish["status"] == "ready"
        ),
        "rollback_searchable": bool(rollback_smoke and rollback_smoke["passed"]),
        "readiness_after_rollback": (
            readiness_after_rollback is not None
            and readiness_after_rollback["status"] == "ready"
        ),
        "final_alias_restored": alias_target(client) == SOURCE_COLLECTION,
        "temporary_collection_removed": not client.collection_exists(
            RESTORED_COLLECTION
        ),
    }
    report = {
        "evaluation_id": "m6-formal-recovery-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "checks": checks,
        "source_collection": SOURCE_COLLECTION,
        "restored_collection": RESTORED_COLLECTION,
        "query_alias": QUERY_ALIAS,
        "final_alias_target": alias_target(client),
        "point_count": source_count,
        "projection_fingerprint_sha256": source_fingerprint,
        "snapshot": {
            "name": snapshot.snapshot_name,
            "qdrant_checksum": snapshot.checksum,
            "external_backup_path": backup_path.relative_to(ROOT).as_posix(),
            "external_backup_sha256": backup_sha256,
            "external_backup_bytes": backup_bytes,
        },
        "after_restored_publish": {
            "readiness": readiness_after_publish,
            "retrieval_smoke": restored_smoke,
        },
        "after_candidate_withdrawal_and_rollback": {
            "readiness": readiness_after_rollback,
            "retrieval_smoke": rollback_smoke,
        },
        "limitations": [
            "Local single-node rehearsal; the external snapshot remains on the same workstation.",
            "The restored candidate contains the same formal corpus version, so this does not test schema migration.",
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
