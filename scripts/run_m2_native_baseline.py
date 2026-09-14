"""CLI for the reproducible M2 PyMuPDF native extraction baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.native_baseline import run_native_baseline


DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval_results"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--artifact-prefix", default="m2-native-pymupdf-1.28.2-v1"
    )
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = run_native_baseline(
        PROJECT_ROOT,
        args.out,
        sample_ids=set(args.sample_ids) if args.sample_ids else None,
        overwrite=args.overwrite,
        artifact_prefix=args.artifact_prefix,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
