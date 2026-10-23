"""Cell-by-cell checks of recognized tables against independent readings.

A table recognized by the layout engine is one reading of the page.  Each
entry of the registry names one table (source sha256 + layout region id),
binds the exact cells it was checked against (``cells_sha256``), lists the
cells whose text an independent reading corrected, and records whether every
cell is now confirmed (``verdict: admit``) or some stay unconfirmed.

Methods:
- ``text_layer_cell_check``: born-digital page; each cell is compared with
  the PDF text layer inside the cell box and takes the text layer's text.
- ``independent_ocr_cell_check``: scanned page; each cell matches what at
  least one of the two independent Paddle runs read inside the cell box.
- ``vision_model_cell_check`` / ``agent_crop_check``: a cell the two runs
  could not settle was confirmed by a vision model agreeing with one run, or
  by the agent comparing the crop with the candidate readings.

A table whose cells no longer hash to ``cells_sha256`` fails the assembly
instead of silently losing its check.  Admitted tables pass review with a
``review_assertion`` binding to the registry, like OCR document admission.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.application.ocr_admission import TABLE_ADMITTABLE_REASONS, TABLE_ADMITTED_REASON

REGISTRY_PATH = Path("data/registry/table-checks.json")
METHODS = frozenset({
    "text_layer_cell_check", "independent_ocr_cell_check",
    "vision_model_cell_check", "agent_crop_check",
})
VERDICTS = frozenset({"admit", "unresolved"})
CHECKED_REASON = "table_cell_check"


class TableCheckError(ValueError):
    """A registry entry does not match exactly one table of its document."""


def cells_sha256(cells: list[dict[str, Any]]) -> str:
    rows = sorted([int(c.get("row") or 0), int(c.get("column") or 0), str(c.get("text") or "")]
                  for c in cells)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def load_table_checks(path: str | Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.is_file():
        return None
    payload = path.read_bytes()
    registry = json.loads(payload)
    if registry.get("schema_version") != "1":
        raise TableCheckError(f"unsupported table-checks schema: {path}")
    seen: set[tuple[str, str]] = set()
    for entry in registry.get("tables") or []:
        key = (str(entry.get("sha256") or ""), str(entry.get("region_id") or ""))
        if len(key[0]) != 64 or not key[1]:
            raise TableCheckError(f"table check needs sha256 and region_id: {key!r}")
        if key in seen:
            raise TableCheckError(f"duplicate table check for {key[1]}")
        seen.add(key)
        if entry.get("method") not in METHODS or entry.get("verdict") not in VERDICTS:
            raise TableCheckError(f"{key[1]}: unknown method or verdict")
        if len(str(entry.get("cells_sha256") or "")) != 64:
            raise TableCheckError(f"{key[1]}: cells_sha256 is required")
        if entry.get("replacement"):
            if entry.get("corrections"):
                raise TableCheckError(f"{key[1]}: a replacement excludes cell corrections")
            _check_grid(key[1], entry["replacement"])
    registry["registry_sha256"] = hashlib.sha256(payload).hexdigest()
    return registry


def _check_grid(region_id: str, replacement: dict[str, Any]) -> None:
    """A replacement grid covers every position exactly once."""
    rows, columns = int(replacement["row_count"]), int(replacement["column_count"])
    covered: set[tuple[int, int]] = set()
    for cell in replacement["cells"]:
        r, c = int(cell["row"]), int(cell["column"])
        rs, cs = int(cell.get("row_span") or 1), int(cell.get("column_span") or 1)
        if r < 0 or c < 0 or rs < 1 or cs < 1 or r + rs > rows or c + cs > columns:
            raise TableCheckError(f"{region_id}: replacement cell r{r}c{c} lies outside the grid")
        for position in ((i, j) for i in range(r, r + rs) for j in range(c, c + cs)):
            if position in covered:
                raise TableCheckError(f"{region_id}: replacement cells overlap at {position}")
            covered.add(position)
    if len(covered) != rows * columns:
        raise TableCheckError(f"{region_id}: replacement leaves grid positions uncovered")


def apply_table_checks(document: dict[str, Any], registry: dict[str, Any] | None) -> int:
    """Apply this document's table checks in place; return admitted tables."""
    if not registry:
        return 0
    sha = str((document.get("source") or {}).get("sha256") or "")
    entries = [e for e in registry.get("tables") or [] if e["sha256"] == sha]
    if not entries:
        return 0
    marker = f"table_cell_check:{registry['registry_sha256'][:12]}"
    tables = {}
    for element in document.get("elements") or []:
        regions = element.get("source_region_ids") or []
        if element.get("type") == "table" and len(regions) == 1:
            tables.setdefault(regions[0], []).append(element)
    admitted = 0
    for entry in entries:
        found = tables.get(entry["region_id"]) or []
        if len(found) != 1:
            raise TableCheckError(
                f"{sha[:12]} {entry['region_id']}: expected one table, found {len(found)}")
        element = found[0]
        cells = (element.get("content") or {}).get("cells") or []
        if cells_sha256(cells) != entry["cells_sha256"]:
            raise TableCheckError(
                f"{sha[:12]} {entry['region_id']}: cells changed since the check")
        replacement = entry.get("replacement")
        if replacement:
            # The recognized grid itself was wrong (merged or shifted
            # cells): the checked table replaces it whole.
            content = element["content"]
            content["row_count"] = int(replacement["row_count"])
            content["column_count"] = int(replacement["column_count"])
            content["cells"] = [{
                "row": int(c["row"]), "column": int(c["column"]),
                "row_span": int(c.get("row_span") or 1), "column_span": int(c.get("column_span") or 1),
                "role": "data", "text": str(c.get("text") or ""), "source_span_ids": [],
            } for c in replacement["cells"]]
        by_position = {(int(c.get("row") or 0), int(c.get("column") or 0)): c for c in cells}
        for fix in entry.get("corrections") or []:
            cell = by_position.get((int(fix["row"]), int(fix["column"])))
            if cell is None or str(cell.get("text") or "") != fix["from"]:
                raise TableCheckError(
                    f"{sha[:12]} {entry['region_id']}: correction r{fix['row']}c{fix['column']} "
                    f"does not match its original cell")
            cell["text"] = fix["to"]
        element["quality_issues"] = [*element.get("quality_issues", []), marker]
        if entry["verdict"] != "admit":
            continue
        reasons = set(element.get("policy_reasons") or [])
        if element.get("quality_status") != "passed" and not reasons <= TABLE_ADMITTABLE_REASONS:
            continue
        assertion = f"review_assertion:table-checks:{registry['registry_sha256'][:12]}"
        if element.get("quality_status") != "passed":
            element["quality_status"] = "passed"
            element["policy_reasons"] = [*element.get("policy_reasons", []),
                                         TABLE_ADMITTED_REASON, CHECKED_REASON, assertion]
            admitted += 1
        element["quality_issues"].append(assertion)
    return admitted
