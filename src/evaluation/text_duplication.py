"""Audit adjacent CJK repetitions without confusing source repetitions."""
from __future__ import annotations

import re
from typing import Any, Iterable


ADJACENT_REPEAT_RE = re.compile(r"([一-鿿，、。；：]{3,12})\1")


def scan_adjacent_repetitions(text: str) -> list[dict[str, Any]]:
    """Return every adjacent repetition matched by the R2c audit rule."""
    return [
        {
            "start": match.start(),
            "end": match.end(),
            "fragment": match.group(1),
            "matched_text": match.group(0),
        }
        for match in ADJACENT_REPEAT_RE.finditer(text or "")
    ]


def audit_chunk_repetitions(
    rows: Iterable[dict[str, Any]],
    source_texts_by_page: dict[int, list[str]],
) -> dict[str, Any]:
    """Classify repeats as source-present (legal) or pipeline-introduced."""
    occurrences: list[dict[str, Any]] = []
    for row in rows:
        body = str(row.get("body") or "")
        for match in scan_adjacent_repetitions(body):
            pages = [int(page) for page in row.get("pages") or []]
            sources = [text for page in pages for text in source_texts_by_page.get(page, [])]
            source_present = any(match["matched_text"] in text for text in sources)
            start, end = int(match["start"]), int(match["end"])
            occurrences.append({
                "chunk_id": row.get("chunk_id"),
                "pages": pages,
                "fragment": match["fragment"],
                "matched_text": match["matched_text"],
                "context": body[max(0, start - 30):min(len(body), end + 30)],
                "judgement": "source_present_legal" if source_present else "pipeline_introduced",
            })
    introduced = sum(row["judgement"] == "pipeline_introduced" for row in occurrences)
    return {
        "count": len(occurrences),
        "source_present_legal_count": len(occurrences) - introduced,
        "pipeline_introduced_count": introduced,
        "occurrences": occurrences,
    }
