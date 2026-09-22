"""Inspect registered PDFs offline; dry-run by default, --write persists the audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.pdf_source_audit import (
    apply_suggestions,
    audit_sources,
    collect_standard_number_conflicts,
    write_audit,
)


def _print_conflict_prompt(conflicts: list[dict]) -> None:
    """Print a human-readable standard-number conflict list to stderr."""
    if not conflicts:
        return
    print("\n=== 标准号冲突提示（建议采用 PDF 内页标准号）===", file=sys.stderr)
    for c in conflicts:
        print(f"\n文件: {c['file_name']}", file=sys.stderr)
        print(f"  文件名标准号: {c['filename_standard_number'] or '（无）'}", file=sys.stderr)
        print(f"  PDF内页标准号候选: {', '.join(c['conflict_values'])}", file=sys.stderr)
        print(f"  建议: 采用内页 {c['suggested_value']}"
              f"（如同意，--apply-suggestions 自动采用；如不同意，请手动改文件名）",
              file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="save provenance registry and audit report")
    parser.add_argument("--details", action="store_true", help="print per-file fields and diagnostics")
    parser.add_argument(
        "--apply-suggestions", action="store_true",
        help="adopt the PDF-extracted (内页) standard number for every conflict, "
             "persisting it as a verified observation; requires --write")
    args = parser.parse_args()
    if args.apply_suggestions and not args.write:
        parser.error("--apply-suggestions requires --write")
    try:
        result = audit_sources(ROOT)
        conflicts = collect_standard_number_conflicts(result)
        _print_conflict_prompt(conflicts)
        applied = []
        if args.apply_suggestions:
            applied = apply_suggestions(result)
        if args.write:
            write_audit(ROOT, result)
    except (ValueError, OSError) as exc:
        # Do not expose exception paths, credentials or raw parser messages.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
    output = {"status": "written" if args.write else "dry_run", **result["summary"],
              "standard_number_conflicts": len(conflicts)}
    if args.apply_suggestions:
        output["suggestions_applied"] = applied
    if args.details:
        output["files"] = result["files"]
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
