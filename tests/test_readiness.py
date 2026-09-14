import unittest

from src.server.readiness import QdrantReadinessProbe


class _FakeQdrantClient:
    def __init__(self, existing):
        self.existing = set(existing)

    def get_collections(self):
        return object()

    def collection_exists(self, name):
        return name in self.existing


class _UnavailableQdrantClient:
    def get_collections(self):
        raise RuntimeError("secret connection detail")


class QdrantReadinessProbeTest(unittest.TestCase):
    def test_all_required_collections_are_ready(self):
        report = QdrantReadinessProbe(
            client=_FakeQdrantClient({"corpus_current", "m3_experiment_current"}),
            required_collections=["corpus_current", "m3_experiment_current"],
        ).check()

        self.assertTrue(report.ready)
        self.assertEqual(report.checks["qdrant"], "ready")
        self.assertEqual(report.checks["corpus_current"], "ready")

    def test_missing_collection_is_not_ready(self):
        report = QdrantReadinessProbe(
            client=_FakeQdrantClient({"corpus_current"}),
            required_collections=["corpus_current", "m3_experiment_current"],
        ).check()

        self.assertFalse(report.ready)
        self.assertEqual(report.checks["m3_experiment_current"], "missing")

    def test_connection_failure_does_not_expose_details(self):
        report = QdrantReadinessProbe(
            client=_UnavailableQdrantClient(),
            required_collections=["corpus_current"],
        ).check()

        self.assertFalse(report.ready)
        self.assertEqual(report.checks, {"qdrant": "unavailable"})
