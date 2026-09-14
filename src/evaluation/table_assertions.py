"""Evaluate approved table facts as atomic OCR-tolerant fragments."""

from __future__ import annotations

import re
from typing import Any


def _normalize_fragment(text: str) -> str:
    value = text.replace("Ⅰ", "I").replace("Ⅱ", "II")
    value = value.replace("ⅰ", "I").replace("ⅱ", "II")
    value = value.lower()
    value = value.replace("毫米", "mm")
    value = value.replace("～", "~").replace("—", "~")
    value = value.replace("≥", ">=").replace("≤", "<=")
    value = re.sub(r"(?<=\d)分", "", value)
    value = re.sub(r"\s+", "", value)
    return value


def evaluate_table_assertion(
    page: dict[str, Any], assertion: dict[str, Any]
) -> dict[str, Any]:
    """Check every approved fragment against one canonical page's evidence text."""

    table_texts = [str(table.get("text") or "") for table in page.get("tables", [])]
    selectors = assertion.get("table_selector_fragments", [])
    if selectors and table_texts:
        normalized_selectors = [_normalize_fragment(item) for item in selectors]
        evidence_parts = [
            text
            for text in table_texts
            if all(item in _normalize_fragment(text) for item in normalized_selectors)
        ]
        scope = "selected_table"
    else:
        evidence_parts = [str(page.get("text") or ""), *table_texts]
        scope = "page_evidence"
    evidence = _normalize_fragment("\n".join(evidence_parts))
    checks = []
    for check in assertion["checks"]:
        missing = [
            fragment
            for fragment in check["required_fragments"]
            if _normalize_fragment(fragment) not in evidence
        ]
        checks.append(
            {
                "check_id": check["check_id"],
                "passed": not missing,
                "missing_fragments": missing,
            }
        )
    return {
        "anchor_id": assertion["anchor_id"],
        "check_count": len(checks),
        "passed_check_count": sum(check["passed"] for check in checks),
        "all_checks_passed": all(check["passed"] for check in checks),
        "checks": checks,
        "scope": scope,
        "matched_table_count": len(evidence_parts) if scope == "selected_table" else None,
        "comparison": "normalized_required_fragments_in_page_evidence",
    }
