"""Authorized download of the E1 reranker -- scope-locked (no expansion).

Per the project owner's explicit authorization (2026-10-06): download ONLY
the six approved files of ``BAAI/bge-reranker-base`` at the pinned commit
``2cfc18c9415c912f9d8155881c133215df768a70``.  The script hard-fails on any
attempt to widen scope: revision and allow_patterns come from the runner
constants, never from arguments.  Afterwards it runs the OFFLINE resolver
(``_resolve_snapshot`` -- the single real path shared with the run) and
records the download scope + actual commit + file bytes into
``E1_DOWNLOAD_RECORD.json`` next to the E1 config; recorded bytes must
equal the metadata-derived sizes in E1_CONFIG or the record is refused.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.run_e1_rerank_experiment import (  # noqa: E402
    E1_CONFIG,
    EXCLUDED_FROM_SCOPE,
    FILTERED_ALLOW_PATTERNS,
    _resolve_snapshot,
)
from src.retrieval.rerank import RerankConfig  # noqa: E402

REPO_ID = "BAAI/bge-reranker-base"
PINNED_SHA = "2cfc18c9415c912f9d8155881c133215df768a70"


def main() -> None:
    e1 = json.loads(E1_CONFIG.read_text(encoding="utf-8"))
    download_meta = e1["model_download"]
    if download_meta["repo_sha"] != PINNED_SHA:
        raise SystemExit(f"REFUSED: E1 config repo_sha != pinned {PINNED_SHA}")
    if download_meta.get("hf_cache_has_reranker") is True:
        raise SystemExit("REFUSED: config claims a cached reranker already")
    if tuple(download_meta["scope"]["allow_patterns"]) != FILTERED_ALLOW_PATTERNS:
        raise SystemExit("REFUSED: E1 config scope != runner FILTERED_ALLOW_PATTERNS")

    print(f"downloading {REPO_ID} @ {PINNED_SHA} -- {len(FILTERED_ALLOW_PATTERNS)} file(s) only")
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=REPO_ID,
        revision=PINNED_SHA,
        allow_patterns=list(FILTERED_ALLOW_PATTERNS),
    )
    print("download finished; verifying via the offline run resolver")

    # offline verification on the SAME path the run uses: pinned revision,
    # filtered patterns, six-file completeness, commit equality
    snapshot_path, commit = _resolve_snapshot(REPO_ID, PINNED_SHA)
    if commit != PINNED_SHA:
        raise SystemExit(f"REFUSED: resolved commit {commit} != pinned {PINNED_SHA}")

    files = []
    for name in FILTERED_ALLOW_PATTERNS:
        target = snapshot_path / name
        files.append({"name": name, "bytes": target.stat().st_size})
    excluded_present = [n for n in EXCLUDED_FROM_SCOPE if (snapshot_path / n).is_file()]

    recorded = {f["name"]: f["bytes"] for f in download_meta["scope"]["files"]}
    actual = {f["name"]: f["bytes"] for f in files}
    if recorded != actual:
        raise SystemExit(
            f"REFUSED: downloaded bytes differ from E1_CONFIG scope: {actual} vs {recorded}"
        )
    if excluded_present:
        raise SystemExit(
            f"REFUSED: excluded files present in the snapshot: {excluded_present}"
        )

    record = {
        "authorization": (
            "GRANTED by project owner 2026-10-06 -- exact scope: "
            f"{REPO_ID} @ {PINNED_SHA}, six filtered files only"
        ),
        "repo_id": REPO_ID,
        "pinned_sha": PINNED_SHA,
        "actual_commit": commit,
        "allow_patterns": list(FILTERED_ALLOW_PATTERNS),
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
        "snapshot_path": str(snapshot_path),
        "excluded_present": excluded_present,
        "verification": "resolved via scripts/run_e1_rerank_experiment.py::_resolve_snapshot (same path as the run)",
    }
    out = E1_CONFIG.parent / "E1_DOWNLOAD_RECORD.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"record: {out}")
    print(json.dumps({k: record[k] for k in ("actual_commit", "total_bytes", "snapshot_path")}, indent=2))


if __name__ == "__main__":
    main()
