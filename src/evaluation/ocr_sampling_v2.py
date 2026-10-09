"""Build deterministic post-fix OCR review worksheets without judgments."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from src.application.v2_chunker import chunk_document
from src.evaluation.ocr_sampling import (
    is_scanned_document,
    render_record_crops,
    sample_record,
    sha256_file,
)
from src.ingestion import column_detection


NATIVE_SAMPLE_SEED = "t6a-native-v2-20261010"
NATIVE_SAMPLE_SIZE = 30


def _area(box: Sequence[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(
        0.0, float(box[3]) - float(box[1])
    )


def _intersection(a: Sequence[float], b: Sequence[float]) -> float:
    return max(0.0, min(float(a[2]), float(b[2])) - max(float(a[0]), float(b[0]))) * max(
        0.0, min(float(a[3]), float(b[3])) - max(float(a[1]), float(b[1]))
    )


def match_chunks_by_page_bboxes(
    page_bboxes: Sequence[dict[str, Any]], chunks: Iterable[Any]
) -> list[tuple[float, Any]]:
    """Rank chunks by coverage of the worksheet's original page rectangles."""
    old = [
        (int(row["physical_page"]), [float(value) for value in row["bbox"]])
        for row in page_bboxes
    ]
    total = sum(_area(box) for _, box in old)
    if total <= 0:
        raise ValueError("page_bboxes have zero area")
    matches: list[tuple[float, Any]] = []
    for chunk in chunks:
        new_by_page: dict[int, list[list[float]]] = defaultdict(list)
        for span in getattr(chunk, "source_spans", []):
            new_by_page[int(span.physical_page)].append(
                [float(value) for value in span.bbox]
            )
        covered = 0.0
        for page, box in old:
            covered += max(
                (_intersection(box, candidate) for candidate in new_by_page.get(page, [])),
                default=0.0,
            )
        score = min(1.0, covered / total)
        if score > 0:
            matches.append((score, chunk))
    return sorted(matches, key=lambda row: (-row[0], str(row[1].chunk_id)))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> str:
    data = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _load_candidate(root: Path, corpus_version: str) -> tuple[dict[str, list[Any]], dict[str, dict]]:
    canonical_dir = root / "data" / "canonical" / "v2" / corpus_version
    chunks: dict[str, list[Any]] = {}
    documents: dict[str, dict] = {}
    for path in sorted(canonical_dir.glob("*.canonical.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        sha = str(document["source"]["sha256"])
        documents[sha] = document
        chunks[sha] = chunk_document(document)
    if not documents:
        raise FileNotFoundError(f"no Canonical documents in {canonical_dir}")
    return chunks, documents


def _assets(root: Path, corpus_version: str) -> dict[str, dict[str, Any]]:
    manifest = json.loads(
        (root / "data" / "registry" / f"{corpus_version}.json").read_text(
            encoding="utf-8"
        )
    )
    return {str(row["sha256"]): row for row in manifest["assets"]}


def _rewrite_crop_paths(record: dict[str, Any], sheet: str) -> None:
    for screenshot in record["screenshots"]:
        for slot_name in ("context", "page_thumbnail"):
            old = Path(screenshot[slot_name]["path"])
            screenshot[slot_name]["path"] = str(
                Path("eval/audits/ocr_sampling/v2/crops") / sheet / old.parent.name / old.name
            ).replace("\\", "/")


def _record_for_chunk(
    chunk: Any,
    *,
    document_name: str,
    document_sha256: str,
    sample_id: str,
    sheet: str,
) -> dict[str, Any]:
    record = sample_record(
        chunk,
        document_name=document_name,
        document_sha256=document_sha256,
        sample_id=sample_id,
    )
    _rewrite_crop_paths(record, sheet)
    return record


def _legacy_reclassified_native_pages(
    root: Path,
    page_corpus_version: str,
    published_corpus_version: str,
) -> dict[str, set[int]]:
    # T4 retained its exact pre-fix detector as audit evidence. Reusing it here
    # identifies the same legacy-two-column -> fixed-single-column population.
    from eval.audits.ocr_fix.analyze_t4_order import _legacy_detect_columns

    published_chunks, _ = _load_candidate(root, published_corpus_version)
    page_docs = _read_jsonl(
        root / "data" / "canonical" / f"{page_corpus_version}-documents.jsonl"
    )
    result: dict[str, set[int]] = {}
    for document in page_docs:
        sha = str(document["sha256"])
        if is_scanned_document(published_chunks.get(sha, [])):
            continue
        pages: set[int] = set()
        for page in document.get("pages") or []:
            boxes = [
                element["bbox"]
                for element in page.get("elements") or []
                if column_detection._is_finite_box(element.get("bbox") or [])
            ]
            legacy = _legacy_detect_columns(
                boxes,
                page_width=float(page.get("width") or 0),
                page_height=float(page.get("height") or 0),
            )
            fixed = column_detection.detect_columns(
                boxes,
                page_width=float(page.get("width") or 0),
                page_height=float(page.get("height") or 0),
            )
            if legacy.column_count == 2 and fixed.column_count == 1:
                pages.add(int(page["physical_page"]))
        if pages:
            result[sha] = pages
    return result


def _chunk_pages(chunk: Any) -> set[int]:
    return {int(span.physical_page) for span in chunk.source_spans}


def _select_native_chunks(
    chunks_by_document: dict[str, list[Any]], eligible_pages: dict[str, set[int]]
) -> list[tuple[str, Any]]:
    groups: dict[str, list[Any]] = {}
    for sha, pages in sorted(eligible_pages.items()):
        candidates = [
            chunk
            for chunk in chunks_by_document.get(sha, [])
            if _chunk_pages(chunk) & pages
            and set((chunk.metadata or {}).get("element_types") or []) == {"text"}
        ]
        groups[sha] = sorted(
            candidates,
            key=lambda chunk: hashlib.sha256(
                f"{NATIVE_SAMPLE_SEED}:{sha}:{chunk.chunk_id}".encode("ascii")
            ).hexdigest(),
        )
    selected: list[tuple[str, Any]] = []
    offsets = {sha: 0 for sha in groups}
    while len(selected) < NATIVE_SAMPLE_SIZE:
        progressed = False
        for sha in sorted(groups):
            offset = offsets[sha]
            if offset < len(groups[sha]):
                selected.append((sha, groups[sha][offset]))
                offsets[sha] += 1
                progressed = True
                if len(selected) == NATIVE_SAMPLE_SIZE:
                    break
        if not progressed:
            raise ValueError(
                f"only {len(selected)} native chunks available; need {NATIVE_SAMPLE_SIZE}"
            )
    return selected


def _render(root: Path, assets: dict[str, dict], rows: Sequence[dict[str, Any]]) -> None:
    for row in rows:
        asset = assets[str(row["document_sha256"])]
        render_record_crops(root, root / asset["canonical_rel_path"], row)


def _screenshot_digest(root: Path, rows: Sequence[dict[str, Any]]) -> str:
    entries = []
    for row in rows:
        for screenshot in row["screenshots"]:
            for name in ("context", "page_thumbnail"):
                slot = screenshot[name]
                if slot.get("failure_reason"):
                    raise ValueError(
                        f"crop failure for {row['sample_id']}: {slot['failure_reason']}"
                    )
                path = root / slot["path"]
                if not path.is_file() or sha256_file(path) != slot.get("sha256"):
                    raise ValueError(f"crop missing or hash mismatch: {path}")
                entries.append(f"{slot['path']}\0{slot['sha256']}")
    return hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()


def build_v2_sample_sheets(
    root: Path,
    *,
    corpus_version: str,
    published_corpus_version: str,
) -> dict[str, Any]:
    chunks_by_document, _ = _load_candidate(root, corpus_version)
    assets = _assets(root, corpus_version)
    old_rows = _read_jsonl(root / "eval/audits/ocr_sampling/sample_sheet.jsonl")
    remapped: list[dict[str, Any]] = []
    for old in old_rows:
        sha = str(old["document_sha256"])
        matches = match_chunks_by_page_bboxes(old["page_bboxes"], chunks_by_document[sha])
        if not matches:
            raise ValueError(f"no positional chunk match for {old['sample_id']}")
        best_score, best = matches[0]
        record = _record_for_chunk(
            best,
            document_name=str(old["document_name"]),
            document_sha256=sha,
            sample_id=str(old["sample_id"]),
            sheet="remapped",
        )
        record.update(
            old_chunk_id=old["chunk_id"],
            new_chunk_id=best.chunk_id,
            match_overlap=round(best_score, 6),
            alternate_matches=[
                {"new_chunk_id": chunk.chunk_id, "match_overlap": round(score, 6)}
                for score, chunk in matches[1:]
            ],
        )
        remapped.append(record)

    eligible_pages = _legacy_reclassified_native_pages(
        root, corpus_version, published_corpus_version
    )
    selected = _select_native_chunks(chunks_by_document, eligible_pages)
    native: list[dict[str, Any]] = []
    per_document: dict[str, int] = defaultdict(int)
    for sha, chunk in selected:
        per_document[sha] += 1
        record = _record_for_chunk(
            chunk,
            document_name=str(assets[sha]["display_file_name"]),
            document_sha256=sha,
            sample_id=f"native-{sha[:12]}-{per_document[sha]:03d}",
            sheet="native",
        )
        record.update(
            old_chunk_id=None,
            new_chunk_id=chunk.chunk_id,
            match_overlap=None,
            t4_reclassified_pages=sorted(_chunk_pages(chunk) & eligible_pages[sha]),
        )
        native.append(record)

    all_rows = remapped + native
    _render(root, assets, all_rows)
    output = root / "eval/audits/ocr_sampling/v2"
    remapped_sha = _write_jsonl(output / "sample_sheet.jsonl", remapped)
    native_sha = _write_jsonl(output / "native_sample_sheet.jsonl", native)
    screenshot_sha = _screenshot_digest(root, all_rows)
    summary = {
        "schema_version": "ocr-sample-sheet-v2-summary-1",
        "corpus_version": corpus_version,
        "published_reference_corpus": published_corpus_version,
        "old_sample_count": len(old_rows),
        "remapped_sample_count": len(remapped),
        "native_sample_count": len(native),
        "native_sample_seed": NATIVE_SAMPLE_SEED,
        "native_reclassified_document_count": len(eligible_pages),
        "native_reclassified_page_count": sum(map(len, eligible_pages.values())),
        "sample_sheet_sha256": remapped_sha,
        "native_sample_sheet_sha256": native_sha,
        "screenshot_file_count": sum(2 * len(row["screenshots"]) for row in all_rows),
        "screenshots_aggregate_sha256": screenshot_sha,
        "judgments_included": False,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def verify_v2_sample_sheets(root: Path, *, corpus_version: str) -> dict[str, Any]:
    output = root / "eval/audits/ocr_sampling/v2"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if summary["corpus_version"] != corpus_version:
        raise ValueError("worksheet corpus version differs from requested candidate")
    remapped = _read_jsonl(output / "sample_sheet.jsonl")
    native = _read_jsonl(output / "native_sample_sheet.jsonl")
    if len(remapped) != 201 or len(native) != NATIVE_SAMPLE_SIZE:
        raise ValueError(f"unexpected row counts: remapped={len(remapped)} native={len(native)}")
    chunks_by_document, _ = _load_candidate(root, corpus_version)
    by_id = {
        (sha, str(chunk.chunk_id)): chunk
        for sha, chunks in chunks_by_document.items()
        for chunk in chunks
    }
    for row in remapped + native:
        chunk = by_id.get((str(row["document_sha256"]), str(row["new_chunk_id"])))
        if chunk is None or row["chunk_text"] != chunk.text:
            raise ValueError(f"candidate chunk mismatch for {row['sample_id']}")
    if sha256_file(output / "sample_sheet.jsonl") != summary["sample_sheet_sha256"]:
        raise ValueError("sample sheet hash mismatch")
    if sha256_file(output / "native_sample_sheet.jsonl") != summary["native_sample_sheet_sha256"]:
        raise ValueError("native sample sheet hash mismatch")
    screenshot_sha = _screenshot_digest(root, remapped + native)
    if screenshot_sha != summary["screenshots_aggregate_sha256"]:
        raise ValueError("screenshot aggregate hash mismatch")
    return {
        "status": "verified",
        "corpus_version": corpus_version,
        "remapped_sample_count": len(remapped),
        "native_sample_count": len(native),
        "screenshot_file_count": summary["screenshot_file_count"],
        "screenshots_aggregate_sha256": screenshot_sha,
    }
