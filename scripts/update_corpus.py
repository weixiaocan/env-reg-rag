"""Refresh the PDF inventory and build or publish the complete corpus.

The command does not discover official sources on the web.  It consumes the
audited source state recorded in ``data/registry/document_reviews.csv``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_publish import publish_corpus_candidate
from src.application.corpus_update import (
    AutomaticPageExtractor,
    CorpusUpdateService,
    build_source_catalog,
    pending_content_pages,
    warm_content_page,
)


def refresh_inventory(project_root: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "data_inventory.py"),
            "--raw-dir",
            "data/raw",
            "--out",
            "data/registry/inventory.csv",
        ],
        cwd=project_root,
        check=True,
    )


def plan(project_root: Path) -> dict[str, object]:
    catalog = build_source_catalog(project_root)
    source_states = Counter(
        asset.metadata.get("source_review", "needs_review") for asset in catalog.assets
    )
    return {
        "source_file_count": catalog.source_file_count,
        "unique_content_count": catalog.unique_content_count,
        "duplicate_copy_count": catalog.duplicate_copy_count,
        "unique_page_count": sum(asset.page_count for asset in catalog.assets),
        "source_review_counts": dict(sorted(source_states.items())),
        "note": (
            "Official-source discovery is not automated; recorded review states "
            "are consumed from data/registry/document_reviews.csv."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh, build and optionally publish the complete PDF corpus"
    )
    parser.add_argument(
        "--plan-only", action="store_true", help="refresh and report without parsing"
    )
    parser.add_argument(
        "--skip-inventory-refresh",
        action="store_true",
        help="use the existing inventory.csv (intended only for diagnostics)",
    )
    parser.add_argument(
        "--ocr",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="OCR pages without a sufficient text layer (default: enabled)",
    )
    parser.add_argument("--minimum-native-characters", type=int, default=20)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="independent PDF workers (default: up to 4)",
    )
    parser.add_argument(
        "--ocr-layout",
        action="store_true",
        help="use slower layout recognition for OCR pages",
    )
    parser.add_argument(
        "--ocr-tables",
        action="store_true",
        help="enable expensive table-structure recognition on every OCR page",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="publish a ready candidate to Qdrant and switch corpus_current",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
    )
    args = parser.parse_args()

    if not args.skip_inventory_refresh:
        refresh_inventory(ROOT)
    print(json.dumps({"stage": "plan", **plan(ROOT)}, ensure_ascii=False, indent=2))
    if args.plan_only:
        return
    if args.ocr and importlib.util.find_spec("paddleocr") is None:
        raise RuntimeError(
            "OCR is enabled but paddleocr is unavailable; install requirements-ocr.txt "
            "or use --no-ocr for a diagnostic candidate"
        )

    def make_extractor() -> AutomaticPageExtractor:
        return AutomaticPageExtractor(
            project_root=ROOT,
            enable_ocr=args.ocr,
            minimum_native_characters=args.minimum_native_characters,
            table_recognition=args.ocr_tables,
            layout_recognition=args.ocr_layout,
        )

    extractor = make_extractor()
    def report_progress(event: dict[str, object]) -> None:
        page = int(event["physical_page"])
        page_count = int(event["page_count"])
        if page == 1 or page == page_count or page % 10 == 0:
            print(json.dumps({"stage": "parse", **event}, ensure_ascii=False), flush=True)

    if args.workers > 1:
        catalog = build_source_catalog(ROOT)
        pending = pending_content_pages(ROOT, catalog, extractor)
        completed = 0
        status_counts: Counter[str] = Counter()
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(
                    warm_content_page,
                    ROOT,
                    asset,
                    physical_page,
                    enable_ocr=args.ocr,
                    minimum_native_characters=args.minimum_native_characters,
                    table_recognition=args.ocr_tables,
                    layout_recognition=args.ocr_layout,
                )
                for asset, physical_page in pending
            ]
            for future in as_completed(futures):
                result = future.result()
                completed += 1
                status_counts[result["decision_status"]] += 1
                if completed == len(pending) or completed % 25 == 0:
                    print(
                        json.dumps(
                            {
                                "stage": "pages_cached",
                                "completed": completed,
                                "total": len(pending),
                                "page_status_counts": dict(sorted(status_counts.items())),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

    manifest = CorpusUpdateService(
        ROOT,
        page_extractor=extractor,
        progress=report_progress,
    ).build()
    summary_keys = (
        "corpus_version",
        "status",
        "source_file_count",
        "unique_content_count",
        "duplicate_copy_count",
        "page_count",
        "page_status_counts",
        "evidence_unit_count",
        "chunk_count",
        "processed_content_count",
        "reused_content_count",
        "canonical_documents",
        "evidence_units",
        "retrieval_chunks",
    )
    print(
        json.dumps(
            {
                "stage": "candidate",
                **{key: manifest[key] for key in summary_keys},
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not args.publish:
        return

    from qdrant_client import QdrantClient

    from src.retrieval.bge_small_zh import BgeSmallZhEmbedder

    result = publish_corpus_candidate(
        ROOT,
        client=QdrantClient(url=args.qdrant_url, timeout=30),
        embedder=BgeSmallZhEmbedder(local_files_only=True, device="cpu"),
    )
    print(json.dumps({"stage": "published", **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
