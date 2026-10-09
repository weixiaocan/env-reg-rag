"""Tamper tests for the miss-probe pre-run binding validation.

The probe (``scripts/probe_miss_rerank.py``) must refuse to diagnose against
anything other than the exact dataset, corpus build and collection that
produced a run's traces.  ``anchor_corpus`` on EVERY dataset row and the
registry pointer's ``chunk_count`` are REQUIRED -- absence is itself a
failure, not a skip.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.probe_miss_rerank import _binding_problems  # noqa: E402

CORPUS = "corpus-test-0123"
COLLECTION = "col__staging__corpus-test-0123__bi-test"


class _StubClient:
    """Duck-typed QdrantClient exposing only what the validator uses."""

    def __init__(self, *, exists: bool = True, points: int | None = 2) -> None:
        self._exists = exists
        self._points = points

    def collection_exists(self, name: str) -> bool:
        return self._exists

    def get_collection(self, name: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(points_count=self._points)


def _write_dataset(path: Path, *, anchor: str | None = CORPUS, rows: int = 2) -> str:
    lines = []
    for i in range(rows):
        row = {"id": f"G{i + 1:03d}", "question": "q", "evidence": []}
        if anchor is not None:
            row["anchor_corpus"] = anchor
        lines.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    tmp_path: Path,
    *,
    config: dict | None = None,
    dataset_anchor: str | None = CORPUS,
    dataset_rows: int = 2,
    pointer: dict | None = None,
    write_dataset: bool = True,
) -> tuple[dict, Path, dict]:
    dataset = tmp_path / "golden.jsonl"
    sha = (
        _write_dataset(dataset, anchor=dataset_anchor, rows=dataset_rows)
        if write_dataset
        else hashlib.sha256(dataset.read_bytes()).hexdigest()
    )
    cfg = {
        "run_id": "diagnostic-probe-gate-test",
        "classification": "diagnostic",
        "dataset_sha256": sha,
        "corpus_version": CORPUS,
        "collection": COLLECTION,
        "point_count": dataset_rows,
    }
    cfg.update(config or {})
    cur = {
        "corpus_version": CORPUS,
        "collection_name": COLLECTION,
        "chunk_count": dataset_rows,
    }
    cur.update(pointer or {})
    return cfg, dataset, cur


def test_clean_fixture_passes_offline_and_live(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path)
    assert _binding_problems(cfg, dataset, cur) == []
    assert _binding_problems(cfg, dataset, cur, client=_StubClient()) == []


def test_dataset_hash_mismatch_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(
        tmp_path, config={"dataset_sha256": "0" * 64}
    )
    problems = _binding_problems(cfg, dataset, cur)
    assert any("dataset hash mismatch" in p for p in problems)


def test_anchor_corpus_required_on_every_row(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path, dataset_anchor=None)
    problems = _binding_problems(cfg, dataset, cur)
    assert any("anchor_corpus missing (required)" in p for p in problems)


def test_anchor_corpus_mismatch_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path, dataset_anchor="corpus-other")
    problems = _binding_problems(cfg, dataset, cur)
    assert any("anchor_corpus" in p and "corpus-other" in p for p in problems)


def test_anchor_corpus_non_uniform_refuses(tmp_path: Path) -> None:
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text(
        json.dumps({"id": "G001", "anchor_corpus": CORPUS}, ensure_ascii=False)
        + "\n"
        + json.dumps({"id": "G002", "anchor_corpus": "corpus-other"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    cfg, ds, cur = _fixture(tmp_path, write_dataset=False)
    problems = _binding_problems(cfg, ds, cur)
    assert any("anchor_corpus not uniform" in p for p in problems)


def test_pointer_corpus_version_mismatch_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(
        tmp_path, pointer={"corpus_version": "corpus-stale"}
    )
    problems = _binding_problems(cfg, dataset, cur)
    assert any("pointer corpus_version" in p for p in problems)


def test_pointer_collection_mismatch_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path, pointer={"collection_name": "col-old"})
    problems = _binding_problems(cfg, dataset, cur)
    assert any("pointer collection" in p for p in problems)


def test_pointer_chunk_count_required(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path, pointer={"chunk_count": None})
    problems = _binding_problems(cfg, dataset, cur)
    assert any("chunk_count missing (required)" in p for p in problems)


def test_config_point_count_required(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path, config={"point_count": None})
    problems = _binding_problems(cfg, dataset, cur)
    assert any("point_count missing (required)" in p for p in problems)


def test_pointer_chunk_count_vs_config_point_count_mismatch_refuses(
    tmp_path: Path,
) -> None:
    cfg, dataset, cur = _fixture(tmp_path, pointer={"chunk_count": 999})
    problems = _binding_problems(cfg, dataset, cur)
    assert any("chunk_count 999" in p and "point_count" in p for p in problems)


def test_missing_collection_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path)
    problems = _binding_problems(
        cfg, dataset, cur, client=_StubClient(exists=False)
    )
    assert any("does not exist" in p for p in problems)


def test_live_point_count_mismatch_refuses(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path)
    problems = _binding_problems(
        cfg, dataset, cur, client=_StubClient(points=4232)
    )
    assert sum("point count mismatch: qdrant" in p for p in problems) == 2


def test_live_point_count_none_treated_as_mismatch(tmp_path: Path) -> None:
    cfg, dataset, cur = _fixture(tmp_path)
    problems = _binding_problems(cfg, dataset, cur, client=_StubClient(points=None))
    assert any("point count mismatch" in p for p in problems)


def test_real_baseline_config_passes_validation() -> None:
    """The real baseline run's config + real dataset + real pointer must pass
    the offline checks (no live client) -- guards against validator drift."""
    run_dir = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001"
    if not run_dir.is_dir():
        pytest.skip("baseline run directory not present")
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    cur = json.loads(
        (ROOT / "data" / "registry" / "corpus-current.json").read_text(encoding="utf-8")
    )
    problems = _binding_problems(cfg, ROOT / "eval" / "golden_v2.jsonl", cur)
    assert problems == []
