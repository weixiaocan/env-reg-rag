"""Build stable evidence units from approved Canonical Document pages."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.evidence import EvidenceLocator, EvidenceUnit


_HEADING_TYPES = {"paragraph_title", "section_header"}
_TABLE_TITLE_TYPES = {"figure_title", "caption", "section_header"}
_IGNORED_TYPES = {"header", "page_header", "page_footer", "number", "table"}
_CLAUSE_START = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千0-9]+[章节条款项]|[0-9]+(?:\s*\.\s*[0-9A-Za-z]+){1,4})(?!\s*[~～—-]\s*\d)(?:\s|[^0-9])"
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
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


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
            "standard_number": metadata.get("standard_number")
            or metadata.get("std_no", ""),
            "document_kind": metadata.get("document_kind", ""),
            "jurisdiction": metadata.get("jurisdiction", ""),
            "effective_status": metadata.get("effective_status", ""),
            "official_source_uri": metadata.get("official_source_uri", ""),
            "source_authority": metadata.get("source_authority", ""),
            "source_review": metadata.get("source_review", "needs_review"),
            "publication_date": metadata.get("publication_date", ""),
            "effective_from": metadata.get("effective_from", ""),
            "local_file_match": metadata.get("local_file_match", ""),
            "selection_status": metadata.get("selection_status", ""),
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
    document: dict[str, Any], page: dict[str, Any], metadata: dict[str, str], registered_regions=None,
) -> dict[str, Any]:
    elements = [
        element for element in page["elements"] if str(element.get("text") or "").strip()
    ]
    elements.extend({'element_id': r['element_id'], 'bbox': r['bbox'], 'text': ''}
                    for r in registered_regions or [])
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


def gated_page_units(document, page, metadata, *, registered_regions=None):
    """Protect identified regions only; legacy unmarked structures are unchanged."""
    marked_tables = [t for t in page.get('tables', []) if 'review_status' in t or 'region_quality' in t]
    typed = [e for e in page['elements'] if e.get('type') in {'formula', 'equation'}]
    regions = list(registered_regions or []) + list(page.get('structured_regions', [])) + typed + marked_tables
    if not regions:
        return _text_units(document, page, metadata) + _table_units(document, page, metadata)

    def box(value):
        return (isinstance(value, (list, tuple)) and len(value) == 4
                and all(type(v) in (int, float) and math.isfinite(v) for v in value)
                and 0 <= value[0] < value[2] <= page.get('width', math.inf)
                and 0 <= value[1] < value[3] <= page.get('height', math.inf))

    for table in page.get('tables', []):
        if table in marked_tables:
            continue
        b = table.get('bbox')
        if (not box(b) or any(not box(r.get('bbox')) for r in regions)
                or any(min(b[2], r['bbox'][2]) > max(b[0], r['bbox'][0])
                       and min(b[3], r['bbox'][3]) > max(b[1], r['bbox'][1]) for r in regions)):
            marked_tables.append(table)
            regions.append(table)
    unknown = any(not box(r.get('bbox')) for r in regions)
    safe, denied = [], []
    for element in page['elements']:
        b = element.get('bbox')
        protected = unknown or not box(b) or element in typed
        if not protected:
            protected = any(min(b[2], r['bbox'][2]) > max(b[0], r['bbox'][0])
                            and min(b[3], r['bbox'][3]) > max(b[1], r['bbox'][1]) for r in regions)
        (denied if protected else safe).append(element)
    seen = {e['element_id'] for e in denied}
    for index, region in enumerate(regions):
        eid = region.get('element_id') or f'region-{index}'
        if eid in seen:
            continue
        seen.add(eid)
        text = region.get('text') or region.get('raw_latex') or '\n'.join(
            str(c.get('text') or '') for c in region.get('cells', [])) or '结构转写未批准，请查看原页。'
        denied.append({'element_id': eid, 'text': text,
                       'bbox': region.get('bbox') if box(region.get('bbox')) else None})
    safe_page = {**page, 'elements': safe, 'tables': [t for t in page.get('tables', []) if t not in marked_tables]}
    units = _text_units(document, safe_page, metadata) + _table_units(document, safe_page, metadata)
    text = '\n'.join(str(e.get('text') or '') for e in denied).strip()
    if text:
        units.append(_make_unit(document, page, metadata, evidence_type='source_locator',
            text=text, elements=denied, heading_path=[], title='结构区域原文定位（未批准转写）',
            usage_policy='source_locator_only', text_reliability='unverified_structured_region'))
    return units


def build_evidence_units(
    project_root: Path,
    canonical_path: Path,
    output_dir: Path,
    *,
    artifact_prefix: str = "m3-evidence-units-v1",
    overwrite: bool = False,
    detected_formula_regions: list[dict[str, Any]] | None = None,
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
    formula_path = root / 'data/registry/formula-gold.json'
    formula_regions = json.loads(formula_path.read_text(encoding='utf-8'))['anchors'] if formula_path.is_file() else []
    detected_by_page = {}
    for region in detected_formula_regions or []:
        detected_by_page.setdefault((region['file_sha256'], region['physical_page']), []).append(region)
    evidence_units: list[dict[str, Any]] = []
    source_pages = 0
    locator_source_pages = 0
    for document in documents:
        metadata = {
            **inventory.get(document["file_name"], {}),
            **document.get("metadata", {}),
        }
        document_sha = (document.get('sha256')
                        or inventory.get(document['file_name'], {}).get('sha256') or '')
        for page in document["pages"]:
            structural = [r for r in page.get('regions', []) if r['kind'] != 'text' and r.get('bbox')]
            detected_by_page.setdefault((document_sha, page['physical_page']), []).extend(
                {'element_id': r['region_id'], 'bbox': r['bbox']} for r in structural)
            for region in structural:
                text = str(region.get('text') or '').strip()
                if region['kind'] == 'table':
                    text = '\n'.join(_table_text(t) for t in region.get('tables', [])) or text
                if region['kind'] == 'formula':
                    text = '\n'.join(c['text'] for c in region.get('context', [])) or text
                # A region without reliable searchable text still has a source locator.
                if not text:
                    text = f"{document['file_name']} 物理页{page['physical_page']} {region['kind']}原图（无可靠关联原文）"
                unit = _make_unit(document, page, metadata, evidence_type='source_locator',
                    text=text, elements=[{'element_id': region['region_id'], 'bbox': region['bbox']}],
                    heading_path=[], title='结构区域原图及原文定位', usage_policy='source_locator_only',
                    text_reliability='unverified_structured_region')
                unit['source_regions'] = [{k: region.get(k) for k in
                    ('region_id', 'file_sha256', 'physical_page', 'kind', 'bbox', 'crop_bbox',
                     'image_ref', 'image_sha256', 'relations', 'execution_status', 'quality_status', 'issues', 'math_category')}]
                evidence_units.append(unit)
            if page["publishable"]:
                source_pages += 1
                source_sha = document_sha
                registered = [{'element_id': 'protected-' + a['anchor_id'], 'bbox': a['bbox']}
                              for a in formula_regions if a['file_sha256'] == source_sha
                              and a['physical_page'] == page['physical_page']]
                registered.extend(detected_by_page.get((source_sha, page['physical_page']), []))
                evidence_units.extend(gated_page_units(document, page, metadata, registered_regions=registered))
            elif page["decision_status"] == "quarantine" and str(
                page.get("text") or ""
            ).strip():
                locator_source_pages += 1
                source_sha = document_sha
                evidence_units.append(_source_locator_unit(document, page, metadata,
                    registered_regions=detected_by_page.get((source_sha,page['physical_page']), [])))

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
        "scope": "approved_evidence_plus_source_locators",
        "region_gate": "identified-structures-locator-only-v1",
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
