"""Build a deduplicated human-adjudication pack for Canonical quality.

Only unresolved *unique source objects* become questions.  Parent roll-ups and
Canonical projections are evidence/impact, never duplicate questions.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _options(kind: str, reason: str) -> list[dict[str, str]]:
    if kind == "text":
        return [
            {"value": "accept_extraction", "label": "原提取正确，接受"},
            {"value": "replace_text", "label": "原提取有误，提供更正文案"},
            {"value": "non_content", "label": "页眉/水印/装饰等非正文"},
        ]
    if kind == "table":
        return [
            {"value": "accept_table", "label": "表结构与全部单元格正确"},
            {"value": "replace_table", "label": "提供更正后的表格/单元格"},
            {"value": "not_a_table", "label": "不是应进入 Canonical 的表格"},
        ]
    if kind == "formula":
        return [
            {"value": "accept_formula", "label": "公式及上下文正确"},
            {"value": "replace_formula", "label": "提供更正后的公式/上下文"},
            {"value": "not_a_formula", "label": "不是应进入 Canonical 的公式"},
        ]
    if kind == "image":
        return [
            {"value": "accept_image", "label": "语义图片、图注与来源关系正确"},
            {"value": "correct_image", "label": "图片/图注/关系需更正"},
            {"value": "decorative_or_carrier", "label": "装饰或页面载体，不单列语义图片"},
        ]
    if "caption" in reason:
        return [
            {"value": "caption_preserved", "label": "标题已被正确保留并关联"},
            {"value": "caption_missing", "label": "标题缺失或关联错误，需修复"},
            {"value": "not_a_caption", "label": "不是语义标题"},
        ]
    if "vector_graphic" in reason or "embedded_image" in reason:
        return [
            {"value": "semantic_visual_missing", "label": "是遗漏的语义图形，需修复"},
            {"value": "already_accounted", "label": "已由现有区域完整承载"},
            {"value": "decorative", "label": "边框/底纹/标志等非语义装饰"},
        ]
    if "math_operator" in reason:
        return [
            {"value": "formula_missing", "label": "是遗漏或错误的公式，需修复"},
            {"value": "already_accounted", "label": "已由现有正文/公式完整承载"},
            {"value": "not_formula", "label": "不是独立公式对象"},
        ]
    return [
        {"value": "accept", "label": "接受现有处理"},
        {"value": "repair", "label": "确认缺陷并提供更正"},
        {"value": "non_content", "label": "非 Canonical 内容"},
    ]


def build_adjudication_items(
    ledger: dict[str, Any],
    documents: list[dict[str, Any]],
    canonicals: list[dict[str, Any]],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one question per unresolved source region or source signal."""
    regions: dict[str, dict[str, Any]] = {}
    pages: dict[tuple[str, int], dict[str, Any]] = {}
    document_names: dict[str, str] = {}
    for document in documents:
        sha = str(document["sha256"])
        document_names[sha] = str(document.get("file_name") or "")
        for page in document.get("pages") or []:
            number = int(page["physical_page"])
            pages[(sha, number)] = page
            for region in page.get("regions") or []:
                regions[str(region["region_id"])] = region

    canonical_by_region: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for canonical in canonicals:
        for element in canonical.get("elements") or []:
            impact = {
                "element_id": element.get("element_id"),
                "element_type": element.get("type"),
                "quality_status": element.get("quality_status"),
                "content": element.get("content"),
            }
            for region_id in element.get("source_region_ids") or []:
                canonical_by_region[str(region_id)].append(impact)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in ledger.get("entries") or []:
        if (entry.get("content_quality_conclusion") != "manual_review_required" or
                entry.get("object_type") not in {"source_region", "source_signal"}):
            continue
        object_id = str(entry["object_id"])
        if object_id in seen:
            raise ValueError(f"duplicate adjudication source object: {object_id}")
        seen.add(object_id)
        identity = entry.get("source_identity") or {}
        sha = str(identity.get("file_sha256") or "")
        number = int(identity.get("physical_page") or 0)
        if not sha or number < 1 or (sha, number) not in pages:
            raise ValueError(f"unresolvable adjudication source identity: {object_id}")
        evidence = entry.get("evidence") or {}
        details = evidence.get("details") or {}
        if entry["object_type"] == "source_region":
            region = regions.get(object_id)
            if region is None:
                raise ValueError(f"source region missing from page-intermediate: {object_id}")
            kind = str(region.get("kind") or identity.get("kind") or "unknown")
            reason = "; ".join(entry.get("uncertainty") or details.get("quality_issues") or
                               ["content_not_independently_verified"])
            extracted = {
                "text": region.get("text"),
                "latex": region.get("latex"),
                "quality_status": region.get("quality_status"),
                "issues": region.get("issues") or [],
                "relations": region.get("relations") or [],
                "crop_assets": region.get("crop_assets") or [],
            }
            affected = sorted(canonical_by_region.get(object_id, []),
                              key=lambda row: str(row.get("element_id") or ""))
        else:
            region = None
            reason = str(identity.get("reason") or details.get("reason") or "unknown_source_signal")
            if "caption" in reason:
                kind = "text"
            elif "math" in reason:
                kind = "formula"
            elif "image" in reason or "vector" in reason:
                kind = "image"
            else:
                kind = "unknown"
            extracted = {
                "original_text": details.get("original_text"),
                "original_text_sha256": details.get("original_text_sha256"),
                "signal_details": details,
            }
            affected = []
        asset = assets.get(sha) or {}
        items.append({
            "item_id": object_id,
            "source_object_type": entry["object_type"],
            "kind": kind,
            "source": {
                "file_name": document_names.get(sha) or asset.get("display_file_name"),
                "file_sha256": sha,
                "pdf_rel_path": asset.get("canonical_rel_path"),
                "physical_page": number,
                "bbox": identity.get("bbox") or (region or {}).get("bbox"),
                "region_id": object_id if entry["object_type"] == "source_region" else None,
                "signal_id": identity.get("signal_id"),
                "source_index": identity.get("source_index"),
            },
            "question": {
                "specific_doubt": reason,
                "uncertainty": entry.get("uncertainty") or [],
                "methods_already_run": evidence.get("methods") or [],
                "suggested_options": _options(kind, reason),
                "required_comment_when_correcting": True,
            },
            "extracted_result": extracted,
            "impact": {
                "affected_canonical_elements": affected,
                "affected_canonical_element_count": len(affected),
                "if_accepted": "record an explicit human ruling bound to this source object",
                "if_repaired": "rebuild page-intermediate and Canonical through the unified pipeline",
            },
            "evidence_input_hashes": evidence.get("input_hashes") or {},
        })
    items.sort(key=lambda row: (
        str(row["source"]["file_sha256"]), int(row["source"]["physical_page"]),
        str(row["source_object_type"]), str(row["item_id"])))
    return items


def json_bytes(value: Any, *, indent: int | None = 2) -> bytes:
    suffix = "\n" if indent is not None else ""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=indent) + suffix).encode("utf-8")

