"""Surgical QXT-489 re-extract -> recovery override pages (T06 state machine).

RETIRED capability: the previous version of this script rewrote the PUBLISHED
page-intermediate jsonl in place (tmp + replace) and cleared caches -- an
untracked, unverifiable mutation of released artifacts. That formal release
path is DISABLED.

T06 replacement: this script only WRITES recovery override pages under
``data/canonical/recovery/<sha>/`` (checkpoint-integrity protected) plus a
recovery record. The recovered pages enter the corpus exclusively as an
explicit input of a unified ``build_corpus.py`` run (``recovery_dir``), which
derives a NEW corpus version from the changed documents -- published
artifacts and the current pointer are never touched by this script.

Usage (from repo root, venv active)::

    PYTHONPATH=src python scripts/reparse_qxt489_p3.py            # write recovery pages
    PYTHONPATH=src python scripts/build_corpus.py --recovery-dir data/canonical/recovery
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.corpus_update import (  # noqa: E402
    AutomaticPageExtractor,
    build_source_catalog,
    write_recovery_pages,
)
from src.ingestion.text_quality import document_text_quality  # noqa: E402

SHA = "89bb2c0c080e232e6cac515f5809b1b3452971afdf52918701ae1d5e7312a004"


def main() -> int:
    catalog = build_source_catalog(ROOT)
    asset = next(a for a in catalog.assets if a.sha256 == SHA)
    print(json.dumps({"asset": asset.display_file_name, "pages": asset.page_count,
                      "path": asset.canonical_rel_path}, ensure_ascii=False), flush=True)

    extractor = AutomaticPageExtractor(
        project_root=ROOT,
        enable_ocr=True,
        minimum_native_characters=20,
        table_recognition=False,
        layout_recognition=False,
    )
    new_pages = []
    for physical_page in range(1, asset.page_count + 1):
        page = extractor.extract(asset, physical_page=physical_page)
        new_pages.append(page)
        sample = " ".join((page.get("text") or "").split())[:80]
        print(json.dumps(
            {"page": physical_page, "route": page.get("extraction_route"),
             "status": page.get("decision_status"),
             "reasons": page.get("decision_reasons"), "sample": sample},
            ensure_ascii=False), flush=True)

    record = write_recovery_pages(ROOT, SHA, new_pages)
    print(json.dumps({"recovery_record": record,
                      "next_step": (
                          "run a unified build with --recovery-dir "
                          "data/canonical/recovery to derive a NEW corpus version")},
                     ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
