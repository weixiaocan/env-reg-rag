"""Publish and roll back immutable Qdrant corpus collections through an alias."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from qdrant_client import QdrantClient, models


@dataclass(frozen=True)
class CorpusSnapshot:
    source_collection_name: str
    snapshot_name: str
    checksum: str | None


class QdrantCorpusReleaseManager:
    """Keep the query-facing alias stable while corpus collections change."""

    def __init__(
        self,
        *,
        client: QdrantClient,
        snapshot_download_base_url: str = "http://127.0.0.1:6333",
    ) -> None:
        self._client = client
        self._snapshot_download_base_url = snapshot_download_base_url.rstrip("/")

    def publish(self, *, collection_name: str, alias_name: str) -> None:
        self._switch(collection_name=collection_name, alias_name=alias_name)

    def rollback(self, *, collection_name: str, alias_name: str) -> None:
        self._switch(collection_name=collection_name, alias_name=alias_name)

    def create_snapshot(self, *, collection_name: str) -> CorpusSnapshot:
        description = self._client.create_snapshot(collection_name, wait=True)
        if description is None:
            raise RuntimeError(f"Qdrant did not create a snapshot for {collection_name}")
        return CorpusSnapshot(
            source_collection_name=collection_name,
            snapshot_name=description.name,
            checksum=description.checksum,
        )

    def restore_snapshot(
        self,
        *,
        snapshot: CorpusSnapshot,
        restored_collection_name: str,
    ) -> None:
        if self._client.collection_exists(restored_collection_name):
            raise ValueError(
                f"restored collection already exists: {restored_collection_name}"
            )
        source = quote(snapshot.source_collection_name, safe="")
        name = quote(snapshot.snapshot_name, safe="")
        location = (
            f"{self._snapshot_download_base_url}/collections/"
            f"{source}/snapshots/{name}"
        )
        self._client.recover_snapshot(
            collection_name=restored_collection_name,
            location=location,
            checksum=snapshot.checksum,
            wait=True,
        )

    def _switch(self, *, collection_name: str, alias_name: str) -> None:
        if not self._client.collection_exists(collection_name):
            raise ValueError(f"release collection does not exist: {collection_name}")

        current = next(
            (
                item
                for item in self._client.get_aliases().aliases
                if item.alias_name == alias_name
            ),
            None,
        )
        if current is not None and current.collection_name == collection_name:
            return

        operations = []
        if current is not None:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias_name)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=alias_name,
                )
            )
        )
        self._client.update_collection_aliases(operations)
