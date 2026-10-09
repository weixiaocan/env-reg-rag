"""Pending-evidence review worksheets for golden_v2 closure.

Read-only with respect to the golden dataset, the corpus, Qdrant and the
published pointer.  For every unclosed manual-pending marker this emits a
worksheet containing exactly what a reviewer needs to rule on it:

* the original PDF page (path, physical page, rendered PNG),
* the actual carrying chunk (chunk_id, full text, pages, usage_policy),
* the quote and the independent rendering transcription,
* every expected fact with its coverage on the quote AND on the whole
  carrying chunk (a fact present in the chunk but outside the quote window
  is a different ruling than a fact absent entirely),
* rendering-vs-quote similarity for pdf_rendering_faithfulness items.

Outputs:
* ``eval/audits/pending_closure/worksheets.json``  (machine-readable)
* ``eval/audits/pending_closure/renders/<doc-tag>-p<NN>.png`` (page renders)

Usage:
    PYTHONPATH=. python scripts/review_pending_evidence.py [--only G023:0,G030:1]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402  (pymupdf)
from qdrant_client import QdrantClient  # noqa: E402

from scripts.reanchor_golden_v2 import Corpus, _fact_coverage, _normalized, _similarity  # noqa: E402

OUT_DIR = ROOT / "eval" / "audits" / "pending_closure"
RENDER_DIR = OUT_DIR / "renders"
DATASET = ROOT / "eval" / "golden_v2.jsonl"
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _scroll_chunks(collection: str) -> list[dict[str, Any]]:
    client = QdrantClient(url="http://127.0.0.1:6333", timeout=120)
    points: list[dict[str, Any]] = []
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection, limit=512, offset=offset, with_payload=True
        )
        for record in records:
            payload = record.payload or {}
            points.append(
                {
                    "chunk_id": payload.get("chunk_id"),
                    "text": payload.get("text") or "",
                    "element_ids": payload.get("element_ids") or [],
                    "pages": payload.get("physical_pages") or [],
                    "usage_policy": payload.get("usage_policy"),
                    "doc_file": payload.get("doc_file") or payload.get("file_name"),
                }
            )
        if offset is None:
            break
    return points


def _carrying_chunk(evidence: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The chunk whose text contains the normalized quote and whose element
    set covers the evidence ids (whitespace-insensitive, per contract test)."""
    ids = set(evidence.get("element_ids") or [])
    needle = _normalized(evidence.get("quote") or "")[:40]
    best: dict[str, Any] | None = None
    best_key: tuple[int, int] | None = None
    for chunk in chunks:
        if chunk["doc_file"] != evidence.get("doc_file"):
            continue
        if not ids <= set(chunk["element_ids"]):
            continue
        if needle and needle not in _normalized(chunk["text"]):
            continue
        # prefer the tightest element match (fewest extra elements, then smaller)
        key = (len(set(chunk["element_ids"]) - ids), -len(chunk["element_ids"]))
        if best_key is None or key < best_key:
            best, best_key = chunk, key
    return best


def _render_pdf_page(pdf_path: Path, physical_page: int, out_png: Path) -> str | None:
    if not pdf_path.exists():
        return None
    try:
        doc = fitz.open(pdf_path)
        if physical_page < 1 or physical_page > doc.page_count:
            doc.close()
            return None
        pix = doc.load_page(physical_page - 1).get_pixmap(matrix=fitz.Matrix(2, 2))
        out_png.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out_png)
        doc.close()
        return str(out_png.relative_to(ROOT))
    except Exception:  # noqa: BLE001 -- render failures must not kill the worksheet
        return None


def _doc_tag(file_name: str) -> str:
    stem = re.sub(r"[^\w一-鿿]+", "_", Path(file_name).stem)
    return re.sub(r"_+", "_", stem).strip("_")[:24]


def build_worksheets(only: list[tuple[str, int]] | None = None) -> list[dict[str, Any]]:
    cases = _load_cases()
    corpus = Corpus("corpus-0b9a1fd84e3d")
    current = json.loads((ROOT / "data" / "registry" / "corpus-current.json").read_text(encoding="utf-8"))
    chunks = _scroll_chunks(current["collection_name"])

    # source pdf path per display file name
    raw = (ROOT / "data" / "canonical" / "corpus-0b9a1fd84e3d-documents.jsonl").read_bytes().decode("utf-8")
    pdf_paths: dict[str, str] = {}
    for line in raw.split("\n"):
        if not line.strip():
            continue
        row = json.loads(line)
        src = (row.get("source_files") or [{}])[0]
        pdf_paths[row["file_name"]] = src.get("rel_path") or ""

    worksheets: list[dict[str, Any]] = []
    rendered: set[str] = set()
    for case in cases:
        for index, evidence in enumerate(case.get("evidence") or []):
            pendings = evidence.get("pending") or []
            if not pendings:
                continue
            if only and (case["id"], index) not in only:
                continue
            doc_file = evidence.get("doc_file") or ""
            page_no = int(evidence.get("page") or 0)
            chunk = _carrying_chunk(evidence, chunks)
            page_text = (corpus.page_texts.get(doc_file) or {}).get(page_no, "")
            tag = _doc_tag(doc_file)
            render_rel = None
            render_key = f"{tag}:{page_no}"
            if render_key not in rendered:
                render_rel = _render_pdf_page(
                    ROOT / pdf_paths.get(doc_file, ""), page_no,
                    RENDER_DIR / f"{tag}-p{page_no:02d}.png",
                )
                rendered.add(render_key)
            elif (RENDER_DIR / f"{tag}-p{page_no:02d}.png").exists():
                render_rel = str((RENDER_DIR / f"{tag}-p{page_no:02d}.png").relative_to(ROOT))
            quote = evidence.get("quote") or ""
            rendering = evidence.get("rendering") or ""
            facts = case.get("expected_facts") or []
            worksheets.append(
                {
                    "case": case["id"],
                    "evidence_index": index,
                    "markers": pendings,
                    "question": case["question"],
                    "answerability": case.get("expected_answerability"),
                    "case_status": case.get("status"),
                    "evidence": {
                        "doc_file": doc_file,
                        "page": page_no,
                        "element_ids": evidence.get("element_ids"),
                        "must_hit": evidence.get("must_hit"),
                        "usage_policy": evidence.get("usage_policy"),
                        "quote_kind": evidence.get("quote_kind"),
                        "quote": quote,
                        "rendering": rendering,
                    },
                    "pdf": {
                        "path": pdf_paths.get(doc_file),
                        "physical_page": page_no,
                        "page_render": render_rel,
                    },
                    "page_text": page_text,
                    "carrying_chunk": (
                        {
                            "chunk_id": chunk["chunk_id"],
                            "pages": chunk["pages"],
                            "usage_policy": chunk["usage_policy"],
                            "element_count": len(chunk["element_ids"]),
                            "text": chunk["text"],
                        }
                        if chunk
                        else None
                    ),
                    "fact_coverage": [
                        {
                            "fact": fact,
                            "on_quote": _fact_coverage(fact, [quote]),
                            "on_chunk": (
                                _fact_coverage(fact, [chunk["text"]]) if chunk else None
                            ),
                        }
                        for fact in facts
                    ],
                    "rendering_similarity": (
                        _similarity(rendering, quote) if rendering and quote else None
                    ),
                }
            )
    return worksheets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="",
        help="comma list like G023:0,G030:1 to restrict worksheets",
    )
    args = parser.parse_args()
    only = None
    if args.only:
        only = set()
        for token in args.only.split(","):
            case_id, idx = token.split(":")
            only.add((case_id, int(idx)))
    worksheets = build_worksheets(only)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "worksheets.json").write_text(
        json.dumps(worksheets, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"worksheets: {len(worksheets)} -> {OUT_DIR / 'worksheets.json'}")
    counts: dict[str, int] = {}
    for ws in worksheets:
        for marker in ws["markers"]:
            counts[marker] = counts.get(marker, 0) + 1
    print("by marker:", json.dumps(counts, ensure_ascii=False))
    missing = [f"{ws['case']}[{ws['evidence_index']}]" for ws in worksheets if ws["carrying_chunk"] is None]
    print("carrying chunk NOT found:", missing or "none")


if __name__ == "__main__":
    main()
