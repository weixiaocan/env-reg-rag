#!/usr/bin/env python
"""Build or verify the T6a post-fix OCR review worksheets."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.ocr_sampling_v2 import (  # noqa: E402
    build_v2_sample_sheets,
    verify_v2_sample_sheets,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--published-corpus-version", default="corpus-0b9a1fd84e3d")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_only:
        result = verify_v2_sample_sheets(ROOT, corpus_version=args.corpus_version)
    else:
        result = build_v2_sample_sheets(
            ROOT,
            corpus_version=args.corpus_version,
            published_corpus_version=args.published_corpus_version,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
