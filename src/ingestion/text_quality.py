"""Shared readability scoring for native OCR text and corpus release gates.

Used by native PDF quality, garble scan, assemble hard-fail, and chunk usage_policy.
"""
from __future__ import annotations

import re
import string
from pathlib import Path
from typing import Any

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_SYMBOL_CHARS = set(string.punctuation) | set("`~^")


def _symbol_count(compact: str) -> int:
    n = 0
    for ch in compact:
        if ch in _SYMBOL_CHARS:
            n += 1
        elif 0xA1 <= ord(ch) <= 0xFF and not ch.isalnum():
            n += 1
    return n


def score_text(text: str) -> dict[str, Any]:
    """Score a text blob; reasons are empty when the text looks like readable content.

    T08 amendment: dot-leader runs (>=3 '.'/'\u2026'/'\u00b7' in a row -- table-of-contents
    page furniture) are removed from the scored content; they are not mojibake
    symbols. And a low Han ratio ALONE is not evidence of corruption for
    legitimately non-Chinese-dominant pages (English contents pages, standards
    reference lists): ``low_han_ratio`` additionally requires corruption
    evidence (replacement characters, glyph markers, or a high symbol ratio).
    """
    raw_compact = re.sub(r"\s", "", text or "")
    leader_stripped = re.sub(r"[.\u2026\u00b7]{3,}", "", raw_compact)
    leader_char_count = len(raw_compact) - len(leader_stripped)
    compact = leader_stripped
    character_count = len(compact)
    replacement_count = compact.count("\ufffd")
    glyph_marker_count = len(re.findall(r"/G\d+", text or ""))
    han_count = len(_HAN_RE.findall(compact))
    symbol_count = _symbol_count(compact)
    replacement_ratio = replacement_count / character_count if character_count else 0.0
    han_ratio = han_count / character_count if character_count else 0.0
    symbol_ratio = symbol_count / character_count if character_count else 0.0
    corruption_evidence = bool(replacement_count or glyph_marker_count
                               or symbol_ratio >= 0.25)
    reasons: list[str] = []
    if not character_count:
        reasons.append("empty_text")
    elif character_count < 8:
        reasons.append("too_short")
    if replacement_count:
        reasons.append("replacement_character_ratio")
    if glyph_marker_count:
        reasons.append("encoded_glyph_markers")
    if character_count >= 20 and han_ratio < 0.12 and symbol_ratio >= 0.25:
        reasons.append("mojibake_symbol_text")
    elif character_count >= 40 and han_ratio < 0.15 and corruption_evidence:
        reasons.append("low_han_ratio")
    status = "fail" if reasons else "pass"
    return {
        "status": status,
        "reasons": reasons,
        "metrics": {
            "non_whitespace_characters": character_count,
            "replacement_characters": replacement_count,
            "replacement_character_ratio": round(replacement_ratio, 6),
            "encoded_glyph_markers": glyph_marker_count,
            "leader_dot_characters": leader_char_count,
            "han_characters": han_count,
            "han_ratio": round(han_ratio, 6),
            "symbol_characters": symbol_count,
            "symbol_ratio": round(symbol_ratio, 6),
        },
    }


def page_reason_label(page: dict[str, Any]) -> str | None:
    """Return a short bad-page label, or None when the page is acceptable."""
    status = (page.get("decision_status") or "").strip()
    text = page.get("text") or ""
    if status == "quarantine":
        reasons = page.get("decision_reasons") or []
        if "visual_blank_page" in reasons or not str(text).strip():
            return "blank_page"
    scored = score_text(text)
    if scored["status"] == "pass":
        return None
    reasons = scored["reasons"]
    if "mojibake_symbol_text" in reasons:
        return "mojibake_symbol"
    if "low_han_ratio" in reasons:
        return "low_han"
    if "empty_text" in reasons:
        return "empty"
    if "too_short" in reasons:
        return "too_short"
    return reasons[0] if reasons else "unreadable"


def document_text_quality(page_doc: dict[str, Any]) -> dict[str, Any]:
    """Aggregate page-level readability into a document verdict."""
    pages = page_doc.get("pages") or []
    bad_reasons: dict[str, int] = {}
    bad_pages = 0
    emptyish = 0
    ok_pages = 0
    sample_page = None
    sample_label = None
    sample = ""
    total_han = 0
    total_chars = 0
    for page in pages:
        label = page_reason_label(page)
        scored = score_text(page.get("text") or "")
        metrics = scored["metrics"]
        total_han += metrics["han_characters"]
        total_chars += metrics["non_whitespace_characters"]
        if label is None:
            ok_pages += 1
            continue
        bad_pages += 1
        bad_reasons[label] = bad_reasons.get(label, 0) + 1
        if label in {"blank_page", "empty", "too_short"}:
            emptyish += 1
        if sample_page is None:
            sample_page = page.get("physical_page")
            sample_label = label
            sample = re.sub(r"\s+", " ", (page.get("text") or ""))[:180]
    page_count = len(pages) or 1
    bad_ratio = bad_pages / page_count
    overall_han_ratio = (total_han / total_chars) if total_chars else 0.0
    if bad_pages == 0:
        verdict = "readable"
    elif bad_ratio <= 0.15 and overall_han_ratio >= 0.35:
        verdict = "mostly_readable"
    elif overall_han_ratio < 0.05 and bad_pages >= 3:
        verdict = "garbled_text"
    elif bad_ratio >= 0.5 and overall_han_ratio < 0.2:
        verdict = "garbled_text"
    else:
        verdict = "mixed"
    display = page_doc.get("file_name") or ""
    rel = page_doc.get("canonical_rel_path") or ""
    if rel:
        display = Path(rel).name
    md = page_doc.get("metadata") or {}
    std = ""
    for ident in md.get("identifiers") or []:
        if ident.get("scheme") == "standard_number":
            std = ident.get("value") or ""
            break
    return {
        "display": display,
        "std": std,
        "pages": len(pages),
        "ok_pages": ok_pages,
        "bad_pages": bad_pages,
        "emptyish_pages": emptyish,
        "bad_page_ratio": round(bad_ratio, 4),
        "verdict": verdict,
        "bad_reasons": bad_reasons,
        "overall_han_ratio": round(overall_han_ratio, 6),
        "sample_page": sample_page,
        "sample_label": sample_label,
        "sample": sample,
        "sha256": page_doc.get("sha256") or "",
    }


def is_answerable_text(text: str) -> bool:
    """True when chunk text is safe to put into the LLM answer context."""
    return score_text(text)["status"] == "pass"
