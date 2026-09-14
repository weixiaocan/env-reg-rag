"""Render auditable page overlays from Canonical Document coordinates."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image, ImageDraw


_STATUS_COLORS = {
    "approved": (22, 163, 74),
    "needs_manual_review": (217, 119, 6),
    "quarantine": (220, 38, 38),
    "skip_blank": (100, 116, 139),
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _inventory_paths(project_root: Path) -> dict[str, Path]:
    inventory_path = project_root / "data" / "registry" / "inventory.csv"
    with inventory_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row["file_name"]: project_root / Path(row["rel_path"])
            for row in csv.DictReader(handle)
        }


def _draw_bbox(
    draw: ImageDraw.ImageDraw,
    bbox: list[float] | None,
    *,
    x_scale: float,
    y_scale: float,
    y_offset: int,
    color: tuple[int, int, int],
    width: int,
) -> bool:
    if bbox is None:
        return False
    x0, y0, x1, y1 = bbox
    draw.rectangle(
        (
            round(x0 * x_scale),
            round(y0 * y_scale) + y_offset,
            round(x1 * x_scale),
            round(y1 * y_scale) + y_offset,
        ),
        outline=color,
        width=width,
    )
    return True


def build_canonical_page_overlays(
    project_root: Path,
    canonical_path: Path,
    output_dir: Path,
    *,
    dpi: int = 120,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render one PNG per canonical sample page plus a traceability manifest."""

    root = Path(project_root).resolve()
    canonical_path = Path(canonical_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError("overlay manifest exists; use overwrite or a new directory")
    if dpi <= 0:
        raise ValueError("dpi must be positive")

    source_paths = _inventory_paths(root)
    documents = _read_jsonl(canonical_path)
    pages_manifest: list[dict[str, Any]] = []
    header_height = 24
    open_documents: dict[Path, pymupdf.Document] = {}
    try:
        for document in documents:
            source_path = source_paths[document["file_name"]]
            pdf = open_documents.setdefault(source_path, pymupdf.open(source_path))
            for page in document["pages"]:
                pdf_page = pdf[int(page["physical_page"]) - 1]
                pixmap = pdf_page.get_pixmap(dpi=dpi, alpha=False)
                source_image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
                canvas = Image.new(
                    "RGB", (source_image.width, source_image.height + header_height), "white"
                )
                canvas.paste(source_image, (0, header_height))
                draw = ImageDraw.Draw(canvas)
                status = page["decision_status"]
                color = _STATUS_COLORS[status]
                label = (
                    f"{page['sample_id']} | page {page['physical_page']} | {status} | "
                    f"{page['extraction_route']} | {page['parser_name']}"
                )
                draw.rectangle((0, 0, canvas.width, header_height - 1), fill=(248, 250, 252))
                draw.text((6, 6), label, fill=color)
                x_scale = source_image.width / float(page["width"])
                y_scale = source_image.height / float(page["height"])
                element_box_count = sum(
                    _draw_bbox(
                        draw,
                        element.get("bbox"),
                        x_scale=x_scale,
                        y_scale=y_scale,
                        y_offset=header_height,
                        color=color,
                        width=2,
                    )
                    for element in page["elements"]
                )
                table_cell_box_count = sum(
                    _draw_bbox(
                        draw,
                        cell.get("bbox"),
                        x_scale=x_scale,
                        y_scale=y_scale,
                        y_offset=header_height,
                        color=(37, 99, 235),
                        width=1,
                    )
                    for table in page["tables"]
                    for cell in table["cells"]
                )
                overlay_file = f"{page['sample_id']}__{status}.png"
                canvas.save(output_dir / overlay_file, format="PNG", optimize=True)
                pages_manifest.append(
                    {
                        "sample_id": page["sample_id"],
                        "file_name": document["file_name"],
                        "physical_page": page["physical_page"],
                        "decision_status": status,
                        "publishable": page["publishable"],
                        "extraction_route": page["extraction_route"],
                        "parser_name": page["parser_name"],
                        "element_box_count": element_box_count,
                        "table_cell_box_count": table_cell_box_count,
                        "overlay_file": overlay_file,
                    }
                )
    finally:
        for pdf in open_documents.values():
            pdf.close()

    summary = {
        "page_count": len(pages_manifest),
        "overlay_count": len(pages_manifest),
        "status_counts": dict(
            sorted(Counter(item["decision_status"] for item in pages_manifest).items())
        ),
    }
    result = {
        "canonical_source": canonical_path.relative_to(root).as_posix(),
        "dpi": dpi,
        "legend": {
            "element_bbox": "decision-status color",
            "table_cell_bbox": "blue",
            "status_colors_rgb": {key: list(value) for key, value in _STATUS_COLORS.items()},
        },
        "summary": summary,
        "pages": pages_manifest,
    }
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result
