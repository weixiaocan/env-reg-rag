"""Generate page-level signals used to select the fixed M2 PDF sample."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_VERSION = "1"


class _WarningCollector(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _image_count(page) -> int:
    resources = page.get("/Resources") or {}
    xobjects = resources.get("/XObject") or {}
    count = 0
    for ref in xobjects.values():
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Image":
            count += 1
    return count


def probe_pages(project_root: Path) -> tuple[list[dict], dict]:
    registry = project_root / "data" / "registry"
    manifest_path = registry / "experiment-sample-v0.json"
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8-sig")
    )
    if manifest.get("status") != "approved_for_experiment":
        raise ValueError("experiment manifest is not approved_for_experiment")
    with (registry / "inventory.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        inventory = {row["file_name"]: row for row in csv.DictReader(handle)}

    rows = []
    input_documents = []
    warnings = _WarningCollector()
    pypdf_logger = logging.getLogger("pypdf")
    pypdf_logger.addHandler(warnings)
    for selected in sorted(manifest["documents"], key=lambda item: item["order"]):
        file_name = selected["file_name"]
        inventory_row = inventory[file_name]
        approved_hash = selected["sha256"].lower()
        if inventory_row["sha256"].lower() != approved_hash:
            raise ValueError(f"manifest and inventory SHA-256 differ: {file_name}")
        source = project_root / inventory_row["rel_path"]
        actual_hash = _sha256(source)
        if actual_hash != approved_hash:
            raise ValueError(f"local PDF no longer matches approved SHA-256: {file_name}")
        input_documents.append({"file_name": file_name, "sha256": actual_hash})
        reader = PdfReader(source)
        for index, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
                preview = re.sub(r"\s+", " ", text).strip()[:120]
                image_count = _image_count(page)
                probe_status = "ok"
                probe_error = ""
            except Exception as exc:
                text = ""
                preview = ""
                image_count = 0
                probe_status = "failed"
                probe_error = f"{type(exc).__name__}: {exc}"[:200]
            rows.append(
                {
                    "manifest_id": manifest["manifest_id"],
                    "document_sha256": approved_hash,
                    "manifest_order": selected["order"],
                    "file_name": file_name,
                    "page_number": index + 1,
                    "probe_status": probe_status,
                    "probe_error": probe_error,
                    "char_count": len(text),
                    "non_whitespace_chars": len(re.sub(r"\s", "", text)),
                    "image_count": image_count,
                    "width_pt": round(float(page.mediabox.width), 2),
                    "height_pt": round(float(page.mediabox.height), 2),
                    "rotation": page.get("/Rotate", 0),
                    "text_preview": preview,
                }
            )
    pypdf_logger.removeHandler(warnings)
    warning_counts = Counter(re.sub(r"\d+", "<n>", message) for message in warnings.messages)
    metadata = {
        "probe_version": PROBE_VERSION,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": _sha256(manifest_path),
        "script_sha256": _sha256(Path(__file__)),
        "python_version": sys.version.split()[0],
        "pypdf_version": importlib.metadata.version("pypdf"),
        "document_count": len(input_documents),
        "page_count": len(rows),
        "failed_page_count": sum(row["probe_status"] == "failed" for row in rows),
        "warning_count": len(warnings.messages),
        "warning_patterns": dict(sorted(warning_counts.items())),
        "inputs": input_documents,
    }
    return rows, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe approved M2 PDF pages")
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "data" / "registry" / "m2-page-probe.csv",
    )
    parser.add_argument("--meta-out", type=Path)
    args = parser.parse_args()
    rows, metadata = probe_pages(PROJECT_ROOT)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    meta_out = args.meta_out or args.out.with_suffix(".meta.json")
    meta_out.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"probed {len(rows)} pages -> {args.out}")
    print(f"metadata -> {meta_out}")


if __name__ == "__main__":
    main()
