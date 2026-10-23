"""Check every table of an assembled candidate cell by cell; write the registry.

Born-digital pages: each cell is compared with the PDF text layer inside the
cell box and takes that text when they differ (the text layer is the
publisher's own text).  Scanned pages: each cell must equal what at least one
of the two independent Paddle runs read inside the cell box.  Tables whose
cells are all confirmed and whose page text all falls inside some cell are
admitted; the rest are written as ``unresolved`` with the disputed cells, for
the vision-model and crop checks that follow.

Entries made by those later checks are kept when this script reruns.

    python scripts/check_tables.py --corpus-version corpus-c742eb0dbb94
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from glob import glob
from typing import Any

import pymupdf

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from src.application.table_checks import REGISTRY_PATH, cells_sha256  # noqa: E402

DETERMINISTIC = {"text_layer_cell_check", "independent_ocr_cell_check"}
RULES = {
    "text_layer_cell_check": (
        "Born-digital page. Each cell is compared with the PDF text layer inside its box; a "
        "differing cell takes the text-layer text when the two are similar (ratio >= 0.5). "
        "Admitted when every cell has a box, every text-layer character in the table falls "
        "inside a cell, and no cell differs beyond that."),
    "independent_ocr_cell_check": (
        "Scanned page. Each cell must equal, ignoring whitespace and width forms, the text one "
        "of the two independent Paddle runs (preprocessed / raw image) read inside the cell box. "
        "Admitted when every cell matches and no OCR line of either run lies outside all cells."),
}


_SUPERSCRIPT = str.maketrans("0123456789-+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺")


def norm(text: str | None) -> str:
    # Underscores are how OCR renders a printed fill-in blank; the text layer
    # has no character there.
    text = unicodedata.normalize("NFKC", (text or "").replace("_", ""))
    text = re.sub(r"[～〜]", "~", text)
    text = re.sub(r"[—–－]", "-", text)
    return re.sub(r"\s+", "", text)


def _inside(box: list[float], cell: list[float], pad: float = 0.0) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return cell[0] - pad <= cx <= cell[2] + pad and cell[1] - pad <= cy <= cell[3] + pad


def _reading(items: list[tuple[list[float], str]]) -> str:
    rows: list[list[tuple[list[float], str]]] = []
    for box, text in sorted(items, key=lambda item: ((item[0][1] + item[0][3]) / 2, item[0][0])):
        height = max(1.0, box[3] - box[1])
        if rows and abs((rows[-1][-1][0][1] + rows[-1][-1][0][3]) / 2
                        - (box[1] + box[3]) / 2) <= 0.5 * height:
            rows[-1].append((box, text))
        else:
            rows.append([(box, text)])
    lines = [re.sub(r" +", " ", "".join(t for _, t in sorted(row, key=lambda item: item[0][0]))).strip()
             for row in rows]
    lines = [line for line in lines if line]
    out = ""
    for line in lines:
        if out and not (re.match(r"[一-鿿（(]", line[:1]) and re.search(r"[一-鿿）)]$", out)):
            out += " "
        out += line
    return out


def _table_box(element: dict[str, Any]) -> tuple[int, list[float]]:
    span = next((s for s in element["source_spans"] if s.get("role") == "primary"),
                element["source_spans"][0])
    return int(span["physical_page"]), list(span["bbox"])


def grid_problems(cells: list[dict[str, Any]], tolerance: float = 3.0) -> list[str]:
    """Cell boxes must agree with the row/column grid the table claims.

    Every cell edge on the page is a grid line.  A cell spanning n rows must
    contain exactly n - 1 row lines strictly inside it (likewise columns), and
    cells that start on the same row must start at the same height.  Two
    printed cells merged into one ("6" over "7") or a box filed under the
    wrong row breaks this even when its text reads right.
    """
    boxed = [c for c in cells if c.get("source_bbox")]
    if len(boxed) != len(cells):
        return []
    problems = []
    for axis, start, span, lo, hi in (("row", "row", "row_span", 1, 3), ("column", "column", "column_span", 0, 2)):
        lines: list[float] = []
        for c in boxed:
            for edge in (c["source_bbox"][lo], c["source_bbox"][hi]):
                if not any(abs(edge - line) <= tolerance for line in lines):
                    lines.append(edge)
        for c in boxed:
            a, b = c["source_bbox"][lo], c["source_bbox"][hi]
            inside = sum(1 for line in lines if a + tolerance < line < b - tolerance)
            if inside + 1 != int(c.get(span) or 1):
                problems.append(f"grid_{axis}_span:r{c['row']}c{c['column']}")
        starts: dict[int, list[float]] = {}
        for c in boxed:
            starts.setdefault(int(c[start]), []).append(c["source_bbox"][lo])
        for index, values in starts.items():
            if max(values) - min(values) > 2 * tolerance:
                problems.append(f"grid_{axis}_start:{index}")
    order = sorted({int(c["row"]) for c in boxed})
    tops = [min(c["source_bbox"][1] for c in boxed if int(c["row"]) == r) for r in order]
    if tops != sorted(tops):
        problems.append("grid_row_order")
    return problems[:10]


def check_ocr(element, lines_by_source):
    _, box = _table_box(element)
    cells = element["content"].get("cells") or []
    inside = {source: [line for line in lines if line.get("bbox") and _inside(line["bbox"], box, 3)]
              for source, lines in lines_by_source.items()}
    pending, problems = [], []
    if not cells or not all(c.get("source_bbox") for c in cells):
        problems.append("cells_without_boxes")
    else:
        for source, lines in inside.items():
            stray = [line["text"] for line in lines if len(norm(line["text"])) >= 2
                     and not any(_inside(line["bbox"], c["source_bbox"], 1.0) for c in cells)]
            if stray:
                problems.append(f"{source}_text_outside_cells:{'|'.join(stray[:5])}")
        for cell in cells:
            reads = {source: _reading([(line["bbox"], line["text"]) for line in lines
                                       if _inside(line["bbox"], cell["source_bbox"])])
                     for source, lines in inside.items()}
            mine = norm(cell.get("text"))
            if any(norm(text) == mine for text in reads.values()):
                continue
            pending.append({"row": cell["row"], "column": cell["column"],
                            "cell": cell.get("text") or "", **reads})
    problems += grid_problems(cells)
    return {"method": "independent_ocr_cell_check", "corrections": [],
            "verdict": "admit" if not pending and not problems else "unresolved",
            "pending_cells": pending, "problems": problems,
            "evidence": {"cells": len(cells), "disputed_cells": len(pending)}}


def check_text_layer(element, page):
    _, box = _table_box(element)
    cells = element["content"].get("cells") or []
    chars = []
    for block in page.get_text("rawdict", clip=pymupdf.Rect(box) + (-2, -2, 2, 2))["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                raised = bool(span["flags"] & 1)  # PyMuPDF: superscripted span
                for ch in span["chars"]:
                    c = " " if ch["c"].isspace() else ch["c"]
                    chars.append((list(ch["bbox"]), c.translate(_SUPERSCRIPT) if raised else c))
    corrections, pending, problems = [], [], []
    if not any(c != " " for _, c in chars):
        problems.append("no_text_layer_in_table")
    if not cells or not all(c.get("source_bbox") for c in cells):
        problems.append("cells_without_boxes")
    else:
        stray = [c for b, c in chars if c != " " and _inside(b, box)
                 and not any(_inside(b, cell["source_bbox"], 0.5) for cell in cells)]
        if stray:
            problems.append(f"text_outside_cells:{''.join(stray[:30])}")
        for cell in cells:
            source = _reading([(b, c) for b, c in chars if _inside(b, cell["source_bbox"], 0.5)])
            current = cell.get("text") or ""
            if norm(source) == norm(current):
                continue
            ratio = difflib.SequenceMatcher(None, norm(source), norm(current)).ratio()
            if norm(source) and ratio >= 0.5:
                corrections.append({"row": cell["row"], "column": cell["column"],
                                    "from": current, "to": source})
            else:
                pending.append({"row": cell["row"], "column": cell["column"],
                                "cell": current, "text_layer": source})
    problems += grid_problems(cells)
    if pending or problems:
        # A correction is trusted only when the whole table lines up with
        # the text layer; otherwise the cell box itself may be wrong.
        pending += [{"row": c["row"], "column": c["column"], "cell": c["from"],
                     "text_layer": c["to"]} for c in corrections]
        corrections = []
    return {"method": "text_layer_cell_check", "corrections": corrections,
            "verdict": "admit" if not pending and not problems else "unresolved",
            "pending_cells": pending, "problems": problems,
            "evidence": {"cells": len(cells), "corrected_cells": len(corrections)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--independent-ocr-run-id", default="ocrv6-c454d03f07c7e5c4")
    parser.add_argument("--raw-independent-ocr-run-id", default="ocrv6-ef8e83933c6bd4f3")
    args = parser.parse_args()
    runs = {"pp_clean": args.independent_ocr_run_id, "pp_raw": args.raw_independent_ocr_run_id}
    run_dir = os.path.join(_REPO_ROOT, "data", "model_runtime", "independent_ocr", "runs")
    registry_path = os.path.join(_REPO_ROOT, REGISTRY_PATH)
    kept = []
    if os.path.isfile(registry_path):
        with open(registry_path, encoding="utf-8") as fh:
            kept = [e for e in json.load(fh)["tables"] if e["method"] not in DETERMINISTIC]
    kept_keys = {(e["sha256"], e["region_id"]) for e in kept}
    pdfs = {}
    entries = []
    for path in sorted(glob(os.path.join(_REPO_ROOT, "data", "canonical", "v2",
                                         args.corpus_version, "*.canonical.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        sha = doc["source"]["sha256"]
        for element in doc["elements"]:
            regions = element.get("source_region_ids") or []
            if element.get("type") != "table" or len(regions) != 1:
                continue
            if (sha, regions[0]) in kept_keys:
                continue
            page_no, _ = _table_box(element)
            ocr = {source: os.path.join(run_dir, run, "pages", sha, f"p{page_no:04d}.json")
                   for source, run in runs.items()}
            if all(os.path.isfile(p) for p in ocr.values()):
                lines = {}
                for source, p in ocr.items():
                    with open(p, encoding="utf-8") as fh:
                        lines[source] = json.load(fh).get("lines") or []
                result = check_ocr(element, lines)
            else:
                if sha not in pdfs:
                    pdfs[sha] = pymupdf.open(_pdf_path(doc))
                result = check_text_layer(element, pdfs[sha][page_no - 1])
            entries.append({"sha256": sha, "region_id": regions[0], "element_id": element["element_id"],
                            "file_name": doc["source"].get("file_name"), "page": page_no,
                            "cells_sha256": cells_sha256(element["content"].get("cells") or []),
                            **result})
    registry = {
        "schema_version": "1", "check_id": "table-checks-v1",
        "generated_from": args.corpus_version,
        "independent_ocr_runs": runs, "rules": RULES,
        "tables": sorted(kept + entries, key=lambda e: (e["sha256"], e["page"], e["region_id"])),
    }
    with open(registry_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter((e["method"], e["verdict"]) for e in registry["tables"]))
    print("corrected cells:", sum(len(e["corrections"]) for e in registry["tables"]),
          "disputed cells:", sum(len(e.get("pending_cells") or []) for e in registry["tables"]))
    return 0


def _pdf_path(doc: dict[str, Any]) -> str:
    for key in ("path", "raw_path", "source_path"):
        value = (doc.get("source") or {}).get(key)
        if value and os.path.isfile(os.path.join(_REPO_ROOT, value)):
            return os.path.join(_REPO_ROOT, value)
    name = (doc.get("source") or {}).get("file_name")
    hits = glob(os.path.join(_REPO_ROOT, "data", "raw", "**", name), recursive=True)
    if len(hits) != 1:
        raise FileNotFoundError(f"cannot locate the PDF for {name}")
    return hits[0]


if __name__ == "__main__":
    raise SystemExit(main())
