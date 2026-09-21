"""Heading detection and section_path inference for V2 canonical assembly.

Identifies section headings from text + optional font size / indent signals,
assigns a level and confidence, and builds a ``section_path`` chain
(``[{label, title}, ...]`` from outer to inner) aligned with the V2 canonical
schema (see ``docs/v2-canonical-schema-design.md`` section 6).

Rules follow the brief:
  * Core signal = numbering format (``第X章`` / ``第X节`` / ``X.`` / ``X.X`` /
    ``X.X.X``). ``label`` is the number itself; ``title`` is the trailing text.
  * Level from the numbering: 章 -> 1, 节 -> 2, dotted arabic -> segment count.
  * Font-size / indent are secondary signals only.
  * confidence: explicit numbered heading with a title -> >= 0.8; font/indent
    only -> 0.5-0.7; ambiguous -> < 0.5.
  * ``build_section_path`` keeps only high-confidence levels ("不确定时截短，
    不猜测").

Pure functions, standard library only (``re``).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import statistics
from typing import Sequence


__all__ = [
    "HeadingInput",
    "HeadingNode",
    "infer_headings",
    "build_section_path",
    "HIGH_CONFIDENCE",
]


HIGH_CONFIDENCE = 0.8
_MEDIUM_CONFIDENCE = 0.6
_LOW_CONFIDENCE = 0.3


# ---------------------------------------------------------------------------
# Numbering patterns
# ---------------------------------------------------------------------------

# Chinese chapter / section. Label includes the 第X章 / 第X节 token so it
# round-trips to the source document.
_CHINESE_NUM = r"[一二三四五六七八九十百千零〇两\d]+"
CHAPTER_RE = re.compile(rf"^第({_CHINESE_NUM})章(?:[^\d\S]*(.*))?$")
SECTION_RE = re.compile(rf"^第({_CHINESE_NUM})节(?:[^\d\S]*(.*))?$")

# Dotted arabic numbering: "5", "5.4", "5.4.2", optionally with a trailing
# dot ("5.") and a title after whitespace. The lookahead ensures we do not
# match the leading number of an ordinary sentence that happens to start
# with digits followed by other punctuation (e.g. "1）", "(1)").
_DOTTED_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:\.)?(?=[\s一-鿿]|$)")

# Element labels that must NOT be mistaken for section headings: table /
# figure / formula captions ("表7.2.2 ...", "图3 ...", "式5.4.2-2 ...").
_ELEMENT_LABEL_RE = re.compile(r"^(?:表|图|式|公式)\s*\d")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeadingInput:
    """A text block candidate for heading detection.

    ``source_index`` is the element's position in reading order (so callers
    can later ask for the section_path of any reading-order slot). ``bbox``
    is page-points ``[x0, y0, x1, y1]`` (top-left origin).
    """

    text: str
    bbox: tuple[float, float, float, float] | list[float]
    source_index: int = 0
    font_size: float | None = None


@dataclass(frozen=True)
class HeadingNode:
    """A detected heading."""

    label: str
    title: str | None
    level: int
    confidence: float
    source_index: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_title(raw: str | None) -> str | None:
    if raw is None:
        return None
    title = raw.strip()
    # Drop a trailing period that is punctuation, not part of the title.
    title = title.rstrip("。.").strip()
    return title or None


def _segment_level(number: str) -> int:
    """Level = number of dot-separated segments: '5'->1, '5.4'->2, '5.4.2'->3."""
    return number.count(".") + 1


def _median_font_size(items: Sequence[HeadingInput]) -> float | None:
    sizes = [it.font_size for it in items if it.font_size and it.font_size > 0]
    if not sizes:
        return None
    return statistics.median(sizes)


def _indent_ratio(bbox: Sequence[float], page_width: float) -> float:
    if page_width <= 0 or not bbox or len(bbox) < 4:
        return 0.0
    return bbox[0] / page_width


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def infer_headings(items: list[HeadingInput]) -> list[HeadingNode]:
    """Classify each input item, returning a :class:`HeadingNode` per heading.

    Items that are not headings are omitted from the result (but their
    ``source_index`` is preserved on the nodes that are returned).
    """
    median_font = _median_font_size(items)
    nodes: list[HeadingNode] = []

    for item in items:
        text = (item.text or "").strip()
        if not text:
            continue

        # Element captions (表/图/式) are never section headings.
        if _ELEMENT_LABEL_RE.match(text):
            continue

        node = _match_numbered(text, item)
        if node is not None:
            nodes.append(node)
            continue

        # Secondary signals only.
        node = _match_font_indent(text, item, median_font)
        if node is not None:
            nodes.append(node)
            continue

        # Not a heading.
        continue

    return nodes


def _match_numbered(text: str, item: HeadingInput) -> HeadingNode | None:
    # Chinese chapter / section first (they start with 第).
    m = CHAPTER_RE.match(text)
    if m:
        label = f"第{m.group(1)}章"
        title = _clean_title(m.group(2))
        return HeadingNode(
            label=label,
            title=title,
            level=1,
            confidence=0.9 if title else 0.7,
            source_index=item.source_index,
        )

    m = SECTION_RE.match(text)
    if m:
        label = f"第{m.group(1)}节"
        title = _clean_title(m.group(2))
        return HeadingNode(
            label=label,
            title=title,
            level=2,
            confidence=0.9 if title else 0.7,
            source_index=item.source_index,
        )

    m = _DOTTED_RE.match(text)
    if m:
        number = m.group(1)
        rest = text[m.end():].strip()
        # A bare number with trailing dot and no title could be a list marker
        # -> slightly lower confidence.
        title = _clean_title(rest)
        level = _segment_level(number)
        # A bare number with no title is ambiguous (could be a list marker)
        # -> low confidence.
        confidence = 0.9 if title else 0.4
        return HeadingNode(
            label=number,
            title=title,
            level=level,
            confidence=confidence,
            source_index=item.source_index,
        )

    return None


def _match_font_indent(
    text: str, item: HeadingInput, median_font: float | None
) -> HeadingNode | None:
    # Only fire when we have a positive font-size signal AND the text is
    # short (headings are usually short). Without a numbering signal we
    # cannot be confident about the level, so this stays medium confidence.
    if item.font_size and median_font and item.font_size >= 1.15 * median_font:
        if len(text) > 40:
            return None
        return HeadingNode(
            label="",
            title=text,
            level=1,
            confidence=_MEDIUM_CONFIDENCE,
            source_index=item.source_index,
        )
    return None


# ---------------------------------------------------------------------------
# section_path construction
# ---------------------------------------------------------------------------


def build_section_path(
    headings: list[HeadingNode],
    target_index: int,
) -> list[dict]:
    """Return the ``section_path`` for the element at ``target_index``.

    Only headings with ``confidence >= HIGH_CONFIDENCE`` and with
    ``source_index <= target_index`` participate ("不确定时截短，不猜测").
    The path is the ancestor chain (outer -> inner) active at that position.
    """
    active = [
        h
        for h in headings
        if h.confidence >= HIGH_CONFIDENCE and h.source_index <= target_index
    ]
    # Sort by reading order to walk the document forward.
    active.sort(key=lambda h: h.source_index)

    stack: list[HeadingNode] = []
    for h in active:
        # Pop anything at the same or deeper level so the stack stays a
        # strictly increasing level chain (1 -> 2 -> 3 ...).
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)

    return [{"label": h.label, "title": h.title} for h in stack]
