"""Surgical QXT-489 re-extract with P3 native/OCR gates; patch page-intermediate jsonl."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.corpus_update import AutomaticPageExtractor, build_source_catalog
from src.ingestion.text_quality import document_text_quality

SHA = "89bb2c0c080e232e6cac515f5809b1b3452971afdf52918701ae1d5e7312a004"
CORPUS = "corpus-069284e6d60e"
JSONL = ROOT / "data" / "canonical" / f"{CORPUS}-documents.jsonl"


def main() -> int:
    catalog = build_source_catalog(ROOT)
    asset = next(a for a in catalog.assets if a.sha256 == SHA)
    print(json.dumps({"asset": asset.display_file_name, "pages": asset.page_count, "path": asset.canonical_rel_path}, ensure_ascii=False), flush=True)

    # Drop unified text-stage caches so OCR path is forced under new quality rules.
    uni = ROOT / "data" / "model_runtime" / "unified_pdf" / SHA
    if uni.is_dir():
        removed = 0
        for p in uni.rglob("text-*.json"):
            p.unlink()
            removed += 1
        print(json.dumps({"cleared_text_stages": removed}, ensure_ascii=False), flush=True)

    # Also clear corpus page caches for this sha (any config hash).
    pages_root = ROOT / "data" / "canonical" / "pages" / SHA
    if pages_root.is_dir():
        shutil.rmtree(pages_root)
        print(json.dumps({"cleared_page_cache_dir": str(pages_root)}, ensure_ascii=False), flush=True)
    for p in (ROOT / "data" / "canonical" / "assets").glob(f"{SHA}*.json"):
        p.unlink()
        print(json.dumps({"cleared_asset_cache": p.name}, ensure_ascii=False), flush=True)

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
        sample = (page.get("text") or "").replace("\n", " ")[:80]
        print(
            json.dumps(
                {
                    "page": physical_page,
                    "route": page.get("extraction_route"),
                    "status": page.get("decision_status"),
                    "reasons": page.get("decision_reasons"),
                    "sample": sample,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    rows = []
    target = None
    with JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            if doc.get("sha256") == SHA:
                # Keep layout/regions from old pages when present; replace text decision fields.
                old_pages = {p["physical_page"]: p for p in (doc.get("pages") or [])}
                merged = []
                for page in new_pages:
                    old = old_pages.get(page["physical_page"], {})
                    merged_page = dict(old)
                    for key in (
                        "physical_page",
                        "page_index",
                        "display_page_label",
                        "width",
                        "height",
                        "rotation",
                        "extraction_route",
                        "parser_name",
                        "parser_profile",
                        "decision_status",
                        "decision_reasons",
                        "publishable",
                        "text",
                        "elements",
                        "tables",
                        "coordinate_normalizations",
                    ):
                        if key in page:
                            merged_page[key] = page[key]
                    merged.append(merged_page)
                doc["pages"] = merged
                target = doc
            rows.append(doc)

    if target is None:
        raise SystemExit("QXT document not found in jsonl")

    report = document_text_quality(target)
    print(json.dumps({"quality": report}, ensure_ascii=False, indent=2), flush=True)

    tmp = JSONL.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for doc in rows:
            fh.write(json.dumps(doc, ensure_ascii=False) + "\n")
    tmp.replace(JSONL)
    print(json.dumps({"patched": str(JSONL), "verdict": report["verdict"], "bad_pages": report["bad_pages"]}, ensure_ascii=False), flush=True)
    return 0 if report["verdict"] != "garbled_text" else 2


if __name__ == "__main__":
    raise SystemExit(main())
