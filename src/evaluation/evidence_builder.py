"""Build stable evidence units from approved Canonical Document pages."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.evidence import EvidenceLocator, EvidenceUnit


_HEADING_TYPES = {"paragraph_title", "section_header"}
_TABLE_TITLE_TYPES = {"figure_title", "caption", "section_header"}
_IGNORED_TYPES = {"header", "page_header", "page_footer", "number", "table"}
_CLAUSE_START = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千0-9]+[章节条款项]|[0-9]+(?:\s*\.\s*[0-9A-Za-z]+){1,4})(?:\s|[^0-9])"
)
_SHORT_NUMBERED_HEADING = re.compile(r"^\s*[0-9]+\s+[\u4e00-\u9fffA-Za-z]{1,12}\s*$")
_MAX_EVIDENCE_CHARS = 600


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "ev_" + hashlib.sha256(encoded).hexdigest()[:32]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _inventory(project_root: Path) -> dict[str, dict[str, str]]:
    path = project_root / "data" / "registry" / "inventory.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["file_name"]: row for row in csv.DictReader(handle)}


def _locator(page: dict[str, Any], elements: list[dict[str, Any]]) -> EvidenceLocator:
    return EvidenceLocator(
        sample_ids=[page["sample_id"]],
        physical_pages=[int(page["physical_page"])],
        page_indices=[int(page["page_index"])],
        element_ids=[str(element["element_id"]) for element in elements],
        bboxes=[element["bbox"] for element in elements if element.get("bbox")],
    )


def _make_unit(
    document: dict[str, Any],
    page: dict[str, Any],
    metadata: dict[str, str],
    *,
    evidence_type: str,
    text: str,
    elements: list[dict[str, Any]],
    heading_path: list[str],
    title: str = "",
    table: dict[str, Any] | None = None,
    usage_policy: str = "answer_and_citation",
    text_reliability: str = "quality_gate_approved",
) -> dict[str, Any]:
    normalized = _normalize(text)
    content_sha = _content_sha(text)
    locator = _locator(page, elements)
    evidence_id = _stable_id(
        {
            "schema_version": "1",
            "document_version_id": document["document_version_id"],
            "physical_page": page["physical_page"],
            "evidence_type": evidence_type,
            "element_ids": locator.element_ids,
            "content_sha256": content_sha,
        }
    )
    unit = EvidenceUnit(
        schema_version="1",
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        text=text,
        normalized_text=normalized,
        content_sha256=content_sha,
        asset_id=document["asset_id"],
        document_version_id=document["document_version_id"],
        file_name=document["file_name"],
        source_uri=document["source_uri"],
        processing_run_id=document["processing_run_id"],
        quality_status=page["decision_status"],
        usage_policy=usage_policy,
        text_reliability=text_reliability,
        title=title,
        heading_path=heading_path,
        context_scope="page_local_inferred",
        locator=locator,
        document_metadata={
            "standard_number": metadata.get("std_no", ""),
            "document_kind": metadata.get("document_kind", ""),
            "jurisdiction": metadata.get("jurisdiction", ""),
            "effective_status": metadata.get("effective_status", ""),
        },
        table=table,
    )
    return unit.to_dict()


def _text_units(
    document: dict[str, Any], page: dict[str, Any], metadata: dict[str, str]
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    heading_path: list[str] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        text = "\n".join(str(item.get("text") or "").strip() for item in pending).strip()
        if text:
            evidence_type = "clause" if _CLAUSE_START.match(_normalize(text)) else "paragraph"
            units.append(
                _make_unit(
                    document,
                    page,
                    metadata,
                    evidence_type=evidence_type,
                    text=text,
                    elements=pending,
                    heading_path=list(heading_path),
                )
            )
        pending = []

    for element in sorted(page["elements"], key=lambda item: item["reading_order"]):
        text = str(element.get("text") or "").strip()
        element_type = str(element.get("type") or "text")
        if not text:
            continue
        if element_type in _HEADING_TYPES or _SHORT_NUMBERED_HEADING.match(
            _normalize(text)
        ):
            flush()
            heading_path = [_normalize(text)]
            continue
        if element_type in _IGNORED_TYPES or element_type in {"figure_title", "caption"}:
            flush()
            continue
        starts_clause = bool(_CLAUSE_START.match(_normalize(text)))
        if starts_clause and pending:
            flush()
        pending_length = len(_normalize("\n".join(str(item.get("text") or "") for item in pending)))
        if pending and pending_length + 1 + len(_normalize(text)) > _MAX_EVIDENCE_CHARS:
            flush()
        pending.append(element)
    flush()
    return units


def _source_locator_unit(
    document: dict[str, Any], page: dict[str, Any], metadata: dict[str, str]
) -> dict[str, Any]:
    elements = [
        element for element in page["elements"] if str(element.get("text") or "").strip()
    ]
    if not str(page.get("text") or "").strip() or not elements:
        raise ValueError("source locator page must preserve searchable text and elements")
    return _make_unit(
        document,
        page,
        metadata,
        evidence_type="source_locator",
        text=str(page["text"]),
        elements=elements,
        heading_path=[],
        title="原文定位（自动转写未通过质量门控）",
        usage_policy="source_locator_only",
        text_reliability="unverified_automatic_extraction",
    )


def _table_text(table: dict[str, Any]) -> str:
    if str(table.get("markdown") or "").strip():
        return str(table["markdown"]).strip()
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in table["cells"]:
        rows.setdefault(int(cell["row"]), []).append((int(cell["col"]), str(cell["text"])))
    return "\n".join(
        " | ".join(text for _, text in sorted(cells))
        for _, cells in sorted(rows.items())
    )


def _table_units(
    document: dict[str, Any], page: dict[str, Any], metadata: dict[str, str]
) -> list[dict[str, Any]]:
    titles = [
        _normalize(str(element.get("text") or ""))
        for element in sorted(page["elements"], key=lambda item: item["reading_order"])
        if element.get("type") in _TABLE_TITLE_TYPES
        and "表" in str(element.get("text") or "")
    ]
    units = []
    for index, table in enumerate(page["tables"]):
        title = str(table.get("caption") or "").strip()
        if not title and index < len(titles):
            title = titles[index]
        text = _table_text(table)
        table_payload = {
            "element_id": table["element_id"],
            "caption": title,
            "html": table.get("html") or "",
            "markdown": table.get("markdown") or "",
            "cells": table["cells"],
            "unit_context": table.get("unit_context") or [],
            "footnotes": table.get("footnotes") or [],
        }
        table_element = {
            "element_id": table["element_id"],
            "bbox": None,
        }
        units.append(
            _make_unit(
                document,
                page,
                metadata,
                evidence_type="table",
                text=text,
                elements=[table_element],
                heading_path=[title] if title else [],
                title=title,
                table=table_payload,
            )
        )
    return units


def build_evidence_units(
    project_root: Path,
    canonical_path: Path,
    output_dir: Path,
    *,
    artifact_prefix: str = "m3-evidence-units-v1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build citable units plus searchable source locators from canonical pages."""

    root = Path(project_root).resolve()
    canonical_path = Path(canonical_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    units_path = output_dir / f"{artifact_prefix}.jsonl"
    manifest_path = output_dir / f"{artifact_prefix}.manifest.json"
    if (units_path.exists() or manifest_path.exists()) and not overwrite:
        raise FileExistsError("evidence output exists; use overwrite or a new prefix")

    documents = _read_jsonl(canonical_path)
    inventory = _inventory(root)
    evidence_units: list[dict[str, Any]] = []
    source_pages = 0
    locator_source_pages = 0
    for document in documents:
        metadata = inventory[document["file_name"]]
        for page in document["pages"]:
            if page["publishable"]:
                source_pages += 1
                evidence_units.extend(_text_units(document, page, metadata))
                evidence_units.extend(_table_units(document, page, metadata))
            elif page["decision_status"] == "quarantine" and str(
                page.get("text") or ""
            ).strip():
                locator_source_pages += 1
                evidence_units.append(_source_locator_unit(document, page, metadata))

    evidence_ids = [unit["evidence_id"] for unit in evidence_units]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("duplicate evidence IDs generated")
    summary = {
        "source_document_count": len(
            {unit["document_version_id"] for unit in evidence_units}
        ),
        "source_page_count": source_pages,
        "locator_source_page_count": locator_source_pages,
        "evidence_unit_count": len(evidence_units),
        "answer_evidence_unit_count": sum(
            unit["usage_policy"] == "answer_and_citation" for unit in evidence_units
        ),
        "source_locator_unit_count": sum(
            unit["usage_policy"] == "source_locator_only" for unit in evidence_units
        ),
        "evidence_type_counts": dict(
            sorted(Counter(unit["evidence_type"] for unit in evidence_units).items())
        ),
    }
    with units_path.open("w", encoding="utf-8", newline="\n") as handle:
        for unit in evidence_units:
            handle.write(json.dumps(unit, ensure_ascii=False) + "\n")
    manifest = {
        "artifact_id": artifact_prefix,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1",
        "scope": "approved_evidence_plus_quarantined_source_locators",
        "segmentation": {
            "boundary": "canonical_element",
            "max_evidence_chars": _MAX_EVIDENCE_CHARS,
            "context_scope": "page_local_inferred",
        },
        "canonical_source": canonical_path.relative_to(root).as_posix(),
        "canonical_sha256": _sha256(canonical_path),
        "summary": summary,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "evidence_units": evidence_units,
        "summary": summary,
        "manifest": manifest,
    }
