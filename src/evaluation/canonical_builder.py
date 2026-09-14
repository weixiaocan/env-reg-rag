"""Build sample-scope Canonical Documents from the approved M2 route ledger."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.domain.canonical_document import (
    CanonicalDocument,
    CanonicalElement,
    CanonicalPage,
    CanonicalTable,
    CanonicalTableCell,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class _TableCellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: list[CanonicalTableCell] = []
        self.row = -1
        self.col = 0
        self.current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row += 1
            self.col = 0
        elif tag in {"td", "th"}:
            values = dict(attrs)
            self.current = {
                "row_span": int(values.get("rowspan") or 1),
                "col_span": int(values.get("colspan") or 1),
                "parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag not in {"td", "th"} or self.current is None:
            return
        text = re.sub(r"\s+", " ", "".join(self.current["parts"])).strip()
        self.cells.append(
            CanonicalTableCell(
                row=max(self.row, 0),
                col=self.col,
                row_span=self.current["row_span"],
                col_span=self.current["col_span"],
                text=text,
            )
        )
        self.col += self.current["col_span"]
        self.current = None


def _paddle_tables(page: dict[str, Any]) -> list[CanonicalTable]:
    tables = []
    for index, source in enumerate(page.get("tables", [])):
        parser = _TableCellParser()
        parser.feed(source.get("html") or "")
        parser.close()
        tables.append(
            CanonicalTable(
                element_id=source.get("table_id") or f"{page['sample_id']}-table-{index}",
                caption="",
                html=source.get("html") or "",
                markdown="",
                cells=parser.cells,
            )
        )
    return tables


def _bbox_to_top_left(
    bbox: dict[str, Any] | None, *, page_height: float
) -> list[float] | None:
    if not bbox:
        return None
    left, top, right, bottom = (
        float(bbox["l"]),
        float(bbox["t"]),
        float(bbox["r"]),
        float(bbox["b"]),
    )
    if str(bbox.get("coord_origin", "")).upper() == "BOTTOMLEFT":
        top, bottom = page_height - top, page_height - bottom
    return [left, min(top, bottom), right, max(top, bottom)]


def _docling_page(
    source: dict[str, Any], ledger: dict[str, str], fallback: dict[str, Any]
) -> tuple[str, list[CanonicalElement], list[CanonicalTable], float, float, int]:
    document = source["docling_document"]
    page_info = next(iter(document["pages"].values()))
    width = float(page_info["size"]["width"])
    height = float(page_info["size"]["height"])
    elements = []
    for index, item in enumerate(document.get("texts", [])):
        provenance = (item.get("prov") or [{}])[0]
        text = str(item.get("text") or "")
        elements.append(
            CanonicalElement(
                element_id=f"{ledger['sample_id']}-docling-text-{index}",
                type=str(item.get("label") or "text"),
                text=text,
                normalized_text=re.sub(r"\s+", " ", text).strip(),
                page_index=int(ledger["pdf_page"]) - 1,
                bbox=_bbox_to_top_left(provenance.get("bbox"), page_height=height),
                reading_order=index,
            )
        )
    markdown_tables = source.get("tables", [])
    tables = []
    for index, item in enumerate(document.get("tables", [])):
        provenance = (item.get("prov") or [{}])[0]
        cells = []
        for cell in item.get("data", {}).get("table_cells", []):
            cells.append(
                CanonicalTableCell(
                    row=int(cell.get("start_row_offset_idx", 0)),
                    col=int(cell.get("start_col_offset_idx", 0)),
                    row_span=int(cell.get("row_span", 1)),
                    col_span=int(cell.get("col_span", 1)),
                    text=str(cell.get("text") or ""),
                    bbox=_bbox_to_top_left(cell.get("bbox"), page_height=height),
                )
            )
        tables.append(
            CanonicalTable(
                element_id=f"{ledger['sample_id']}-docling-table-{index}",
                caption="",
                html="",
                markdown=(markdown_tables[index].get("text") if index < len(markdown_tables) else ""),
                cells=cells,
            )
        )
    return source["text"], elements, tables, width, height, int(fallback["rotation"])


def _normalized_page_bbox(
    bbox: list[Any], *, width: float, height: float, rotation: int
) -> list[float]:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    points = [(x0, y0), (x1, y1)]
    if rotation == 90:
        points = [(width - y, x) for x, y in points]
    elif rotation == 180:
        points = [(width - x, height - y) for x, y in points]
    elif rotation == 270:
        points = [(y, height - x) for x, y in points]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [
        min(max(min(xs), 0.0), width),
        min(max(min(ys), 0.0), height),
        min(max(max(xs), 0.0), width),
        min(max(max(ys), 0.0), height),
    ]


def _canonical_elements(page: dict[str, Any]) -> list[CanonicalElement]:
    width = float(page["width"])
    height = float(page["height"])
    rotation = int(page["rotation"])
    return [
        CanonicalElement(
            element_id=str(item["element_id"]),
            type=str(item.get("type") or "text"),
            text=str(item.get("text") or ""),
            normalized_text=str(item.get("normalized_text") or ""),
            page_index=int(item["page_index"]),
            bbox=(
                _normalized_page_bbox(
                    item["bbox"], width=width, height=height, rotation=rotation
                )
                if item.get("bbox")
                else None
            ),
            coordinate_origin=str(item.get("coordinate_origin") or "top_left"),
            reading_order=int(item.get("reading_order", 0)),
            heading_path=list(item.get("heading_path") or []),
            clause_path=list(item.get("clause_path") or []),
            parent_id=item.get("parent_id"),
            children_ids=list(item.get("children_ids") or []),
            confidence=item.get("confidence"),
        )
        for item in page.get("elements", [])
    ]


def build_canonical_sample_corpus(
    project_root: Path,
    output_dir: Path,
    *,
    artifact_prefix: str = "m2-canonical-sample-v1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build parser-independent, sample-scope documents from the M2 route ledger."""

    root = Path(project_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    documents_path = output_dir / f"{artifact_prefix}-documents.jsonl"
    manifest_path = output_dir / f"{artifact_prefix}-manifest.json"
    if (documents_path.exists() or manifest_path.exists()) and not overwrite:
        raise FileExistsError("canonical output exists; use overwrite or a new prefix")

    registry = root / "data" / "registry"
    results = root / "data" / "eval_results"
    ledger_path = registry / "m2-page-route-ledger-v1.csv"
    ledger_rows = _read_csv(ledger_path)
    manifest = json.loads((registry / "experiment-sample-v0.json").read_text(encoding="utf-8-sig"))
    inventory = {row["file_name"]: row for row in _read_csv(registry / "inventory.csv")}
    artifact_paths = {
        "data/eval_results/m2-native-pymupdf-1.28.2-v1-pages.jsonl": results / "m2-native-pymupdf-1.28.2-v1-pages.jsonl",
        "data/eval_results/m2-ocr-routed-light-v1-pages.jsonl": results / "m2-ocr-routed-light-v1-pages.jsonl",
        "data/eval_results/m2-ocr-routed-heavy-v1-pages.jsonl": results / "m2-ocr-routed-heavy-v1-pages.jsonl",
        "data/eval_results/m2-docling-2.126.0-v1-pages.jsonl": results / "m2-docling-2.126.0-v1-pages.jsonl",
    }
    pages_by_artifact = {
        name: {page["sample_id"]: page for page in _read_jsonl(path)}
        for name, path in artifact_paths.items()
    }
    native_pages = pages_by_artifact[
        "data/eval_results/m2-native-pymupdf-1.28.2-v1-pages.jsonl"
    ]

    ledger_by_file: dict[str, list[dict[str, str]]] = {}
    for row in ledger_rows:
        ledger_by_file.setdefault(row["file_name"], []).append(row)
    config_hash = _sha256(ledger_path)
    documents = []
    for source_document in manifest["documents"]:
        file_name = source_document["file_name"]
        digest = source_document["sha256"].lower()
        canonical_pages = []
        for ledger in ledger_by_file[file_name]:
            fallback = native_pages[ledger["sample_id"]]
            if ledger["selected_route"] == "skip_blank":
                text, elements, tables = "", [], []
                width, height, rotation = fallback["width"], fallback["height"], fallback["rotation"]
            elif ledger["selected_route"] == "docling_recovery":
                source = pages_by_artifact[ledger["selected_artifact"]][ledger["sample_id"]]
                text, elements, tables, width, height, rotation = _docling_page(
                    source, ledger, fallback
                )
            else:
                source = pages_by_artifact[ledger["selected_artifact"]][ledger["sample_id"]]
                text = source.get("text") or ""
                elements = _canonical_elements(source)
                tables = _paddle_tables(source) if ledger["selected_parser"].startswith("paddleocr") else []
                width, height, rotation = source["width"], source["height"], source["rotation"]
            coordinate_normalizations = []
            if int(rotation):
                coordinate_normalizations.append("bbox_rotation_applied")
            if any(
                item.get("bbox")
                and (
                    min(item["bbox"]) < 0
                    or item["bbox"][2] > float(width)
                    or item["bbox"][3] > float(height)
                )
                for item in (source.get("elements", []) if ledger["selected_route"] != "skip_blank" else [])
            ):
                coordinate_normalizations.append("bbox_clipped_to_page")
            canonical_pages.append(
                CanonicalPage(
                    sample_id=ledger["sample_id"],
                    physical_page=int(ledger["pdf_page"]),
                    page_index=int(ledger["pdf_page"]) - 1,
                    display_page_label=fallback.get("display_page_label"),
                    width=float(width),
                    height=float(height),
                    rotation=int(rotation),
                    extraction_route=ledger["selected_route"],
                    parser_name=ledger["selected_parser"],
                    parser_profile=ledger["selected_profile"],
                    decision_status=ledger["decision_status"],
                    decision_reasons=ledger["decision_reasons"].split("|"),
                    publishable=ledger["decision_status"] == "approved",
                    text=text,
                    elements=elements,
                    tables=tables,
                    raw_artifact_ref=ledger["selected_artifact"],
                    coordinate_normalizations=coordinate_normalizations,
                )
            )
        record = CanonicalDocument(
            schema_version="1",
            scope="m2_representative_pages_only",
            asset_id=f"asset_{digest[:16]}",
            document_version_id=f"doc_{digest[:16]}",
            file_name=file_name,
            sha256=digest,
            source_uri=inventory[file_name].get("official_source_uri") or "",
            processing_run_id=artifact_prefix,
            config_hash=config_hash,
            pages=sorted(canonical_pages, key=lambda page: page.page_index),
        )
        documents.append(record.to_dict())

    summary = {
        "document_count": len(documents),
        "page_count": sum(len(document["pages"]) for document in documents),
        "publishable_page_count": sum(
            page["publishable"] for document in documents for page in document["pages"]
        ),
        "decision_status_counts": dict(
            sorted(
                Counter(
                    page["decision_status"]
                    for document in documents
                    for page in document["pages"]
                ).items()
            )
        ),
    }
    with documents_path.open("w", encoding="utf-8", newline="\n") as handle:
        for document in documents:
            handle.write(json.dumps(document, ensure_ascii=False) + "\n")
    output_manifest = {
        "artifact_id": artifact_prefix,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "m2_representative_pages_only",
        "schema_version": "1",
        "route_ledger_sha256": config_hash,
        "input_artifact_sha256": {
            name: _sha256(path) for name, path in artifact_paths.items()
        },
        "summary": summary,
    }
    manifest_path.write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"documents": documents, "summary": summary, "manifest": output_manifest}
