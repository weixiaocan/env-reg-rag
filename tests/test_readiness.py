"""Public readiness verifies the published pointer against live Qdrant state."""

from types import SimpleNamespace

from src.server.readiness import QdrantReadinessProbe


class _Qdrant:
    def __init__(self, *, alias_target: str, points_count: int) -> None:
        self.alias_target = alias_target
        self.points_count = points_count

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def collection_exists(self, name: str) -> bool:
        return name == "target"

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(
                    alias_name="corpus_v2_live",
                    collection_name=self.alias_target,
                )
            ]
        )

    def get_collection(self, name: str):
        assert name == "target"
        return SimpleNamespace(points_count=self.points_count)


def _probe(client: _Qdrant, *, query_entry: str = "corpus_v2_live"):
    return QdrantReadinessProbe(
        client=client,
        query_entry=query_entry,
        expected_alias="corpus_v2_live",
        expected_collection="target",
        expected_point_count=4232,
    )


def test_readiness_accepts_matching_alias_target_and_point_count() -> None:
    report = _probe(_Qdrant(alias_target="target", points_count=4232)).check()

    assert report.ready is True
    assert set(report.checks.values()) == {"ready"}


def test_readiness_rejects_query_entry_that_bypasses_live_alias() -> None:
    report = _probe(
        _Qdrant(alias_target="target", points_count=4232), query_entry="target"
    ).check()

    assert report.ready is False
    assert report.checks["query_entry"] == "mismatch"


def test_readiness_rejects_alias_target_mismatch() -> None:
    report = _probe(_Qdrant(alias_target="other", points_count=4232)).check()

    assert report.ready is False
    assert report.checks["alias_target"] == "mismatch"


def test_readiness_rejects_point_count_mismatch() -> None:
    report = _probe(_Qdrant(alias_target="target", points_count=4000)).check()

    assert report.ready is False
    assert report.checks["point_count"] == "mismatch"
