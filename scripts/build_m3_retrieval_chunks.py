"""Build M3 retrieval chunks from the versioned evidence units."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.retrieval_chunk_builder import build_retrieval_chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-prefix", default="m3-retrieval-chunks-v1")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = build_retrieval_chunks(
        ROOT,
        ROOT / "data" / "evidence" / "m3-evidence-units-v1.jsonl",
        ROOT / "data" / "retrieval",
        artifact_prefix=args.artifact_prefix,
        overwrite=args.overwrite,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
