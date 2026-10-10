"""Clause-number continuity metrics for Canonical V2 documents."""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any


_THREE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def inspect_clause_continuity(document: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    illegal: list[dict[str, Any]] = []
    for element in document.get("elements") or []:
        if element.get("type") != "text" or element.get("role") != "clause":
            continue
        label = str(element.get("label") or "").strip()
        pages = [int(span.get("physical_page") or 0) for span in element.get("source_spans") or []]
        match = _THREE.fullmatch(label)
        if not match:
            illegal.append({"label": label, "element_id": element.get("element_id"),
                            "pages": sorted(set(pages)), "reason": "expected_N.N.k"})
            continue
        section = f"{match.group(1)}.{match.group(2)}"
        groups[section].append((int(match.group(3)), str(element.get("element_id") or ""), pages[0] if pages else 0))
    missing: list[dict[str, str]] = []
    duplicates: list[dict[str, Any]] = []
    jumps: list[dict[str, Any]] = []
    for section, rows in sorted(groups.items()):
        counts = Counter(number for number, _, _ in rows)
        for number, count in sorted(counts.items()):
            if count > 1:
                occurrences = [{"element_id": eid, "page": page} for n, eid, page in rows if n == number]
                duplicates.append({"section": section, "label": f"{section}.{number}",
                                   "count": count, "occurrences": occurrences})
        unique = sorted(counts)
        for left, right in zip(unique, unique[1:]):
            if right > left + 1:
                jumps.append({"section": section, "from": f"{section}.{left}",
                              "to": f"{section}.{right}", "gap": right - left - 1})
                missing.extend({"section": section, "label": f"{section}.{value}"}
                               for value in range(left + 1, right))
    return {"missing": missing, "duplicates": duplicates, "illegal": illegal,
            "illegal_jumps": jumps, "summary": {
                "missing_count": len(missing), "duplicate_count": len(duplicates),
                "illegal_count": len(illegal), "illegal_jump_count": len(jumps),
            }}
