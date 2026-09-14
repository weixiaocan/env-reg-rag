"""Build versioned M3 evidence units from the approved Canonical sample pages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.evidence_builder import build_evidence_units


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-prefix", default="m3-evidence-units-v1")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = build_evidence_units(
        ROOT,
        ROOT / "data" / "canonical" / "m2-canonical-sample-v1-documents.jsonl",
        ROOT / "data" / "evidence",
        artifact_prefix=args.artifact_prefix,
        overwrite=args.overwrite,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
