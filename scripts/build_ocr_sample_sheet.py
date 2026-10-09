#!/usr/bin/env python
"""Build the current published corpus OCR sampling worksheet and crops."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.ocr_sampling import build_sample_sheet, verify_sample_sheet  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only", action="store_true",
        help="verify the existing worksheet, byte-exact chunk text, and crop hashes",
    )
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify_sample_sheet(ROOT), ensure_ascii=False, indent=2))
        return 0
    summary = build_sample_sheet(ROOT)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["crop_failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
