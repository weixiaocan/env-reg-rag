import unittest

from qdrant_client import QdrantClient, models

from src.retrieval.qdrant_index import QdrantRetrievalIndex
from src.retrieval.qdrant_release import QdrantCorpusReleaseManager


class QdrantCorpusReleaseServiceTest(unittest.TestCase):
    alias_name = "test_m3_corpus_current"
    v1_name = "test_m3_release_v1"
    v2_name = "test_m3_release_v2"
    restored_name = "test_m3_release_restored"

    def setUp(self):
        self.client = QdrantClient(url="http://127.0.0.1:6333", timeout=10)
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        aliases = {item.alias_name for item in self.client.get_aliases().aliases}
        if self.alias_name in aliases:
            self.client.update_collection_aliases(
                [models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.alias_name))]
            )
        if self.client.collection_exists(self.v1_name):
            for snapshot in self.client.list_snapshots(self.v1_name):
                self.client.delete_snapshot(self.v1_name, snapshot.name)
        for name in (self.v1_name, self.v2_name, self.restored_name):
            if self.client.collection_exists(name):
                self.client.delete_collection(name)

    @staticmethod
    def _chunk(version):
        return {
            "chunk_id": f"chunk_{version:032d}",
            "text": f"语料版本 {version}",
            "primary_evidence_id": f"ev_{version:032d}",
            "physical_pages": [version],
            "metadata": {"usage_policy": "answer_and_citation"},
        }

    def test_publish_and_rollback_switch_the_stable_query_alias(self):
        v1 = QdrantRetrievalIndex(client=self.client, collection_name=self.v1_name, vector_size=2)
        v2 = QdrantRetrievalIndex(client=self.client, collection_name=self.v2_name, vector_size=2)
        v1.build([self._chunk(1)], [[1.0, 0.0]])
        v2.build([self._chunk(2)], [[1.0, 0.0]])
        manager = QdrantCorpusReleaseManager(client=self.client)
        current = QdrantRetrievalIndex(
            client=self.client,
            collection_name=self.alias_name,
            vector_size=2,
        )

        manager.publish(collection_name=self.v1_name, alias_name=self.alias_name)
        self.assertEqual(current.search([1.0, 0.0], mode="exact_dense")[0].chunk_id, self._chunk(1)["chunk_id"])

        manager.publish(collection_name=self.v2_name, alias_name=self.alias_name)
        self.assertEqual(current.search([1.0, 0.0], mode="exact_dense")[0].chunk_id, self._chunk(2)["chunk_id"])

        manager.rollback(collection_name=self.v1_name, alias_name=self.alias_name)
        self.assertEqual(current.search([1.0, 0.0], mode="exact_dense")[0].chunk_id, self._chunk(1)["chunk_id"])

    def test_snapshot_restore_recreates_searchable_evidence_and_alias(self):
        source = QdrantRetrievalIndex(
            client=self.client,
            collection_name=self.v1_name,
            vector_size=2,
        )
        source.build([self._chunk(1)], [[1.0, 0.0]])
        manager = QdrantCorpusReleaseManager(client=self.client)

        snapshot = manager.create_snapshot(collection_name=self.v1_name)
        manager.restore_snapshot(
            snapshot=snapshot,
            restored_collection_name=self.restored_name,
        )
        manager.publish(
            collection_name=self.restored_name,
            alias_name=self.alias_name,
        )
        current = QdrantRetrievalIndex(
            client=self.client,
            collection_name=self.alias_name,
            vector_size=2,
        )

        hit = current.search([1.0, 0.0], mode="exact_dense", limit=1)[0]
        self.assertEqual(hit.chunk_id, self._chunk(1)["chunk_id"])
        self.assertEqual(hit.primary_evidence_id, self._chunk(1)["primary_evidence_id"])


if __name__ == "__main__":
    unittest.main()
