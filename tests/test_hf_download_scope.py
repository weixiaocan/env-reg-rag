"""Verification of the precise HF download scope for E1 (no model download).

Pinned properties:

* the E1 config's ``model_download.scope`` is exactly the runner's
  ``FILTERED_ALLOW_PATTERNS`` -- the six files transformers resolves
  per-file for ``AutoModelForSequenceClassification`` +
  ``AutoTokenizer`` -- and excludes the duplicate weight formats and
  non-load files (pytorch_model.bin / onnx / README / .gitattributes);
* the recorded filtered total equals the sum of the recorded file sizes
  (≈1.13 GB, from a read-only metadata query);
* a cache containing ONLY the filtered set resolves every required file
  under ``local_files_only=True`` (both ``huggingface_hub.try_to_load_from_cache``
  and ``transformers.utils.cached_file`` -- the exact resolution entry
  points ``from_pretrained`` uses), and the excluded files do NOT resolve;
* the filtered set is minimal: removing any one file makes that file
  unresolvable, so nothing beyond the recorded scope is a load dependency.

No network access happens in any of these tests -- the fake cache is
constructed locally from stub files (resolution only, no weight loading).
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_e1_rerank_experiment import (  # noqa: E402
    EXCLUDED_FROM_SCOPE,
    FILTERED_ALLOW_PATTERNS,
)

E1_CONFIG = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001" / "E1_CONFIG.json"
REPO_ID = "BAAI/bge-reranker-base"
REPO_SHA = "2cfc18c9415c912f9d8155881c133215df768a70"
EXPECTED_FILTERED_TOTAL_BYTES = 1134374819


def _scope() -> dict:
    config = json.loads(E1_CONFIG.read_text(encoding="utf-8"))
    return config["model_download"]["scope"]


def test_e1_config_scope_matches_code_covers_requirements_excludes_rest() -> None:
    scope = _scope()
    # config ↔ code: one canonical filtered pattern set
    assert tuple(scope["allow_patterns"]) == FILTERED_ALLOW_PATTERNS
    # every required file matches a pattern
    for name in FILTERED_ALLOW_PATTERNS:
        assert any(fnmatch.fnmatch(name, p) for p in scope["allow_patterns"])
    # excluded files are excluded: no pattern matches them
    for name in EXCLUDED_FROM_SCOPE:
        assert not any(fnmatch.fnmatch(name, p) for p in scope["allow_patterns"])
        assert name not in [f["name"] for f in scope["files"]]
    # recorded capacity = sum of recorded file sizes = pinned expectation
    recorded = {f["name"]: f["bytes"] for f in scope["files"]}
    assert set(recorded) == set(FILTERED_ALLOW_PATTERNS)
    assert sum(recorded.values()) == scope["total_bytes"]
    assert scope["total_bytes"] == EXPECTED_FILTERED_TOTAL_BYTES
    # repo pin is an immutable commit sha
    download = json.loads(E1_CONFIG.read_text(encoding="utf-8"))["model_download"]
    assert re.fullmatch(r"[0-9a-f]{40}", download["repo_sha"])
    # single real path: the RUN revision is the SAME pinned repo sha
    config = json.loads(E1_CONFIG.read_text(encoding="utf-8"))
    assert config["variable"]["revision"] == download["repo_sha"]


def _fake_cache(tmp_path: Path, files: list[str]) -> Path:
    cache = tmp_path / "hub"
    model_dir = cache / "models--BAAI--bge-reranker-base"
    snapshot = model_dir / "snapshots" / REPO_SHA
    snapshot.mkdir(parents=True)
    for name in files:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")
    refs = model_dir / "refs"
    refs.mkdir()
    (refs / "main").write_text(REPO_SHA, encoding="utf-8")
    return cache


def test_filtered_cache_resolves_required_and_not_excluded(tmp_path: Path) -> None:
    from huggingface_hub import try_to_load_from_cache
    from transformers.utils import cached_file

    cache = _fake_cache(tmp_path, list(FILTERED_ALLOW_PATTERNS))
    for name in FILTERED_ALLOW_PATTERNS:
        # hub-level resolution
        resolved = try_to_load_from_cache(REPO_ID, name, cache_dir=str(cache))
        assert resolved is not None and Path(resolved).is_file()
        # the exact entry point from_pretrained uses
        assert Path(cached_file(REPO_ID, name, cache_dir=str(cache), local_files_only=True)).is_file()
    for name in EXCLUDED_FROM_SCOPE:
        assert try_to_load_from_cache(REPO_ID, name, cache_dir=str(cache)) is None


@pytest.mark.parametrize("missing", list(FILTERED_ALLOW_PATTERNS))
def test_filtered_scope_is_minimal_missing_file_fails(tmp_path: Path, missing: str) -> None:
    """过滤集恰好极小：缺任一文件即该文件解析失败——不存在的隐性依赖。"""
    from huggingface_hub import try_to_load_from_cache

    files = [f for f in FILTERED_ALLOW_PATTERNS if f != missing]
    cache = _fake_cache(tmp_path, files)
    assert try_to_load_from_cache(REPO_ID, missing, cache_dir=str(cache)) is None
    for name in files:
        assert try_to_load_from_cache(REPO_ID, name, cache_dir=str(cache)) is not None
