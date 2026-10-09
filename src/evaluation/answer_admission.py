"""Fail-closed, offline answer-admission audit primitives.

The audit counts Canonical elements (stable source objects), not locator-only
chunks. Chunks are projections and can repeat one source object or inherit a
stricter policy from a neighbouring element.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable


STRUCTURED_TYPES = {"table", "formula", "figure"}
CONTENT_DEFECT_MARKERS = {
    "garbled_text",
    "mojibake",
    "ocr_failed",
    "ocr_low_confidence",
    "unreadable_text",
    "quarantined_source",
    "projection_text_unanswerable",
}


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _review_assertion_present(element: dict[str, Any]) -> bool:
    for container in (element, element.get("review") or {}, element.get("metadata") or {}):
        if any(container.get(key) for key in ("review_assertion_id", "review_id", "assertion_id")):
            return True
    return False


def _intrinsic_root_cause(element: dict[str, Any]) -> str | None:
    quality = element.get("quality_status")
    issues = {
        str(issue if not isinstance(issue, dict) else issue.get("code") or issue.get("reason") or "")
        for issue in (element.get("quality_issues") or [])
    }
    reasons = {str(reason) for reason in (element.get("policy_reasons") or [])}

    if quality in {"quarantined", "failed"} or issues & CONTENT_DEFECT_MARKERS:
        return "content_quality_defect"
    if element.get("type") in STRUCTURED_TYPES and not _review_assertion_present(element):
        return "default_conservative_policy"
    if quality in {None, "", "needs_review", "unknown"}:
        return "missing_verifiable_quality_evidence"
    if reasons & CONTENT_DEFECT_MARKERS:
        return "content_quality_defect"
    if element.get("usage_policy") == "source_locator_only":
        return "missing_verifiable_quality_evidence"
    return None


def _content_preview(element: dict[str, Any], limit: int = 240) -> str:
    content = element.get("content") or {}
    values: list[str] = []
    for key in ("text", "markdown", "latex", "caption", "html"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return "\n".join(values)[:limit]


def build_admission_audit(
    documents: Iterable[dict[str, Any]],
    chunks: Iterable[Any],
) -> dict[str, Any]:
    """Build a deterministic chunk-to-source-object audit.

    Every chunk reference must resolve to exactly one element in its source
    document. A missing or ambiguous reference fails closed.
    """

    elements: dict[str, dict[str, Any]] = {}
    documents_by_canonical: dict[str, dict[str, Any]] = {}
    for document in documents:
        canonical_id = str(document.get("canonical_id") or "")
        source = document.get("source") or {}
        source_sha = str(source.get("sha256") or "").lower()
        if not canonical_id or len(source_sha) != 64:
            raise ValueError("canonical document missing canonical_id or source sha256")
        documents_by_canonical[canonical_id] = document
        for element in document.get("elements") or []:
            element_id = str(element.get("element_id") or "")
            key = f"{source_sha}:{element_id}"
            if not element_id or key in elements:
                raise ValueError(f"ambiguous source object: {key}")
            elements[key] = {
                "source_object_key": key,
                "source_sha256": source_sha,
                "file_name": source.get("file_name") or "",
                "canonical_id": canonical_id,
                "element_id": element_id,
                "element_type": element.get("type") or "unknown",
                "source_spans": _plain(element.get("source_spans") or []),
                "source_region_ids": list(element.get("source_region_ids") or []),
                "provenance": _plain(element.get("provenance") or []),
                "links": _plain(element.get("links") or []),
                "quality_status": element.get("quality_status"),
                "quality_issues": _plain(element.get("quality_issues") or []),
                "policy_reasons": list(element.get("policy_reasons") or []),
                "content_preview": _content_preview(element),
                "intrinsic_root_cause": _intrinsic_root_cause(element),
                "affected_locator_chunk_ids": [],
                "affected_answer_chunk_ids": [],
            }

    locator_membership: dict[str, set[str]] = defaultdict(set)
    answer_membership: dict[str, set[str]] = defaultdict(set)
    chunk_rows: list[dict[str, Any]] = []
    answer_count = 0
    locator_count = 0

    for chunk in chunks:
        chunk_id = str(_value(chunk, "chunk_id") or "")
        canonical_id = str(_value(chunk, "canonical_id") or "")
        if not canonical_id and len(documents_by_canonical) == 1:
            canonical_id = next(iter(documents_by_canonical))
        document = documents_by_canonical.get(canonical_id)
        if document is None:
            raise ValueError(f"chunk {chunk_id}: unknown canonical_id {canonical_id!r}")
        source_sha = str((document.get("source") or {}).get("sha256") or "").lower()
        source_keys: list[str] = []
        for element_id in list(_value(chunk, "element_ids", []) or []):
            key = f"{source_sha}:{element_id}"
            if key not in elements:
                raise ValueError(f"chunk {chunk_id}: untraceable element {element_id!r}")
            source_keys.append(key)
        if not source_keys:
            raise ValueError(f"chunk {chunk_id}: no source elements")

        metadata = _plain(_value(chunk, "metadata", {}) or {})
        policy = metadata.get("usage_policy") or "source_locator_only"
        if policy == "answer_and_citation":
            answer_count += 1
            for key in source_keys:
                answer_membership[key].add(chunk_id)
        elif policy == "source_locator_only":
            locator_count += 1
            for key in source_keys:
                locator_membership[key].add(chunk_id)

        chunk_rows.append({
            "chunk_id": chunk_id,
            "usage_policy": policy,
            "quality_status": metadata.get("quality_status"),
            "policy_reasons": list(metadata.get("policy_reasons") or []),
            "source_object_keys": source_keys,
            "source_spans": _plain(_value(chunk, "source_spans", []) or []),
        })

    source_rows: list[dict[str, Any]] = []
    locator_keys = set(locator_membership)
    intrinsic_locator = 0
    spread = 0
    for key in sorted(elements):
        row = dict(elements[key])
        row["affected_locator_chunk_ids"] = sorted(locator_membership.get(key, set()))
        row["affected_answer_chunk_ids"] = sorted(answer_membership.get(key, set()))
        cause = row.pop("intrinsic_root_cause")
        if key in locator_keys:
            if cause is None:
                cause = "multi_source_strictest_policy_spread"
                spread += 1
            else:
                intrinsic_locator += 1
        else:
            cause = cause or "admitted"
        row["root_cause"] = cause
        source_rows.append(row)

    return {
        "summary": {
            "chunk_count": len(chunk_rows),
            "answer_and_citation_chunks": answer_count,
            "source_locator_only_chunks": locator_count,
            "unique_source_object_count": len(source_rows),
            "locator_unique_source_object_count": len(locator_keys),
            "intrinsic_locator_source_object_count": intrinsic_locator,
            "strictest_policy_spread_source_object_count": spread,
        },
        "chunks": chunk_rows,
        "source_objects": source_rows,
    }
