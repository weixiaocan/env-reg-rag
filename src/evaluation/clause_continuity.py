"""Clause-number continuity metrics for Canonical V2 documents."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any


_THREE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_COMMENTARY = re.compile(r"^条文说明$")
_NON_CLAUSE_NUMBER = re.compile(
    r"^\s*(?:[（(]\s*\d+\.\d+\.\d+\s*[-－—]\s*\d+\s*[）)]|"
    r"式\s*\d+\.\d+\.\d+(?:\s*[-－—]\s*\d+)?|"
    r"表\s*\d+\.\d+\.\d+(?:\s*[-－—]\s*\d+)?|"
    r"图\s*\d+\.\d+\.\d+(?:\s*[-－—]\s*\d+)?)"
)


def _element_text(element: dict[str, Any]) -> str:
    content = element.get("content") or {}
    return str(content.get("text") or element.get("text") or "").strip()


def _normalize_ocr_joined_clause_label(label: str, text: str) -> str | None:
    """Recover ``4.1.15`` from OCR text such as ``4.1.154 正文``.

    The trailing digit is a detached list/formula marker only when the
    three-digit suffix is printed verbatim at the start of a prose clause.
    Formula/table/figure numbers are filtered separately before this rule.
    """
    match = _THREE.fullmatch(label)
    if match is None or len(match.group(3)) < 3:
        return None
    prefix = match.group(3)[:-1]
    if not prefix or int(prefix) == 0:
        return None
    if not re.match(rf"^{re.escape(label)}\s+[一-鿿]", text):
        return None
    return f"{match.group(1)}.{match.group(2)}.{int(prefix)}"


def inspect_clause_continuity(document: dict[str, Any]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[tuple[int, str, int]]] = defaultdict(list)
    illegal: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    corrected: list[dict[str, Any]] = []
    namespace = "body"
    for element in document.get("elements") or []:
        text = _element_text(element)
        if _COMMENTARY.fullmatch(re.sub(r"\s+", "", text)):
            namespace = "commentary"
        if element.get("type") != "text" or element.get("role") != "clause":
            continue
        label = str(element.get("label") or "").strip()
        pages = [int(span.get("physical_page") or 0) for span in element.get("source_spans") or []]
        if _NON_CLAUSE_NUMBER.match(text):
            excluded.append({
                "namespace": namespace, "label": label,
                "element_id": element.get("element_id"),
                "pages": sorted(set(pages)), "reason": "formula_table_or_figure_number",
            })
            continue
        normalized_label = _normalize_ocr_joined_clause_label(label, text)
        if normalized_label is not None:
            corrected.append({
                "namespace": namespace, "label": label,
                "normalized_label": normalized_label,
                "element_id": element.get("element_id"),
                "pages": sorted(set(pages)), "reason": "ocr_joined_trailing_digit",
            })
            label = normalized_label
        match = _THREE.fullmatch(label)
        if not match:
            illegal.append({"namespace": namespace, "label": label,
                            "element_id": element.get("element_id"),
                            "pages": sorted(set(pages)), "reason": "expected_N.N.k"})
            continue
        section = f"{match.group(1)}.{match.group(2)}"
        groups[(namespace, section)].append(
            (int(match.group(3)), str(element.get("element_id") or ""), pages[0] if pages else 0))
    missing: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    jumps: list[dict[str, Any]] = []
    for (group_namespace, section), rows in sorted(groups.items()):
        counts = Counter(number for number, _, _ in rows)
        for number, count in sorted(counts.items()):
            if count > 1:
                occurrences = [{"element_id": eid, "page": page} for n, eid, page in rows if n == number]
                duplicates.append({"namespace": group_namespace, "section": section,
                                   "label": f"{section}.{number}", "count": count,
                                   "occurrences": occurrences})
        unique = sorted(counts)
        for left, right in zip(unique, unique[1:]):
            if right > left + 1:
                left_pages = [page for number, _, page in rows if number == left]
                right_pages = [page for number, _, page in rows if number == right]
                between_pages = [max(left_pages, default=0), min(right_pages, default=0)]
                jumps.append({"namespace": group_namespace, "section": section,
                              "from": f"{section}.{left}", "to": f"{section}.{right}",
                              "gap": right - left - 1, "between_pages": between_pages})
                missing.extend({"namespace": group_namespace, "section": section,
                                "label": f"{section}.{value}",
                                "between_pages": between_pages}
                               for value in range(left + 1, right))
    namespace_counts = Counter()
    for row in missing:
        namespace_counts[f"{row['namespace']}_missing_count"] += 1
    for row in duplicates:
        namespace_counts[f"{row['namespace']}_duplicate_count"] += 1
    return {"missing": missing, "duplicates": duplicates, "illegal": illegal,
            "excluded_non_clause_numbers": excluded,
            "corrected_ocr_joined_clause_labels": corrected,
            "illegal_jumps": jumps, "summary": {
                "missing_count": len(missing), "duplicate_count": len(duplicates),
                "illegal_count": len(illegal), "illegal_jump_count": len(jumps),
                "excluded_non_clause_number_count": len(excluded),
                "corrected_ocr_joined_clause_label_count": len(corrected),
                "body_missing_count": namespace_counts["body_missing_count"],
                "body_duplicate_count": namespace_counts["body_duplicate_count"],
                "commentary_missing_count": namespace_counts["commentary_missing_count"],
                "commentary_duplicate_count": namespace_counts["commentary_duplicate_count"],
            }}
