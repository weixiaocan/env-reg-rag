"""Inspect registered PDFs offline; dry-run by default, --write persists the audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.pdf_source_audit import audit_sources, write_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="save provenance registry and audit report")
    parser.add_argument("--details", action="store_true", help="print per-file fields and diagnostics")
    args = parser.parse_args()
    try:
        result = audit_sources(ROOT)
        if args.write:
            write_audit(ROOT, result)
    except (ValueError, OSError) as exc:
        # Do not expose exception paths, credentials or raw parser messages.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
    output = {"status": "written" if args.write else "dry_run", **result["summary"]}
    if args.details:
        output["files"] = result["files"]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
