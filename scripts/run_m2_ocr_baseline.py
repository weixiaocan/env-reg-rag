"""CLI for the M2 PaddleOCR PP-StructureV3 baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.ocr_baseline import run_ocr_baseline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--table-recognition",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument(
        "--ocr-profile",
        choices=["text_layout_light", "table_structure_heavy"],
    )
    parser.add_argument(
        "--artifact-prefix", default="m2-ocr-ppstructurev3-3.7.0-v1"
    )
    args = parser.parse_args()

    def report_progress(event: dict) -> None:
        print(
            f"[OCR] {event['completed']}/{event['total']} "
            f"{event['sample_id']} {event['outcome']}",
            file=sys.stderr,
            flush=True,
        )

    result = run_ocr_baseline(
        ROOT,
        ROOT / "data" / "eval_results",
        sample_ids=set(args.sample_ids) if args.sample_ids else None,
        overwrite=args.overwrite,
        artifact_prefix=args.artifact_prefix,
        dpi=args.dpi,
        table_recognition=args.table_recognition,
        ocr_profile=args.ocr_profile,
        resume=args.resume,
        progress_callback=report_progress,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
