"""Render M2 Canonical Document page-location overlays."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.canonical_overlays import build_canonical_page_overlays


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi", type=int, default=120)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = build_canonical_page_overlays(
        ROOT,
        ROOT / "data" / "canonical" / "m2-canonical-sample-v1-documents.jsonl",
        ROOT / "data" / "eval_results" / "m2-canonical-overlays-v1",
        dpi=args.dpi,
        overwrite=args.overwrite,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
