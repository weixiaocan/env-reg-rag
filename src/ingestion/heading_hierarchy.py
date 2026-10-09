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
    "detect_toc_pages",
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
_BODY_END_RE = re.compile(r"[。；;：:，,]\s*$")
_NORMATIVE_VERB_RE = re.compile(
    r"(?:应|宜|不得|不应|必须|可|按下式|符合下列|有关规定执行)"
)


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
    physical_page: int | None = None


@dataclass(frozen=True)
class HeadingNode:
    """A detected heading."""

    label: str
    title: str | None
    level: int
    confidence: float
    source_index: int


# A TOC is a page-level structure. OCR commonly splits one printed entry into
# two blocks ("4.3 Trench support" and "12"), so matching individual lines is
# insufficient and is precisely how TOC entries used to enter the heading
# stack. These patterns are deliberately conservative; a page needs a TOC
# title, two dot-leader entries, or three title/page-number pairs.
_TOC_TITLE_RE = re.compile(r"^(?:目\s*录|目\s*次|contents?)$", re.IGNORECASE)
_TOC_DOT_LEADER_RE = re.compile(
    r"(?:\.{2,}|…{1,}|\u00b7{3,}|_{3,})\s*[（(]?\s*\d{1,4}\s*[）)]?\s*$"
)
_TOC_PAGE_NUMBER_RE = re.compile(r"^[（(]?\s*\d{1,4}\s*[）)]?\s*$")
_TOC_ENTRY_START_RE = re.compile(
    r"^(?:第[一-鿿\d]+章|第[一-鿿\d]+节|\d+(?:\s*\.\s*\d+)*\.?)\s*\S+"
)


def detect_toc_pages(items: Sequence[HeadingInput]) -> set[int]:
    """Return physical pages whose text stream is a table of contents.

    The detector handles both intact entries (dot leaders followed by a page
    number) and the split-block output produced by the OCR pipeline. It does
    not classify a normal numbered heading on its own as TOC content.
    """
    by_page: dict[int, list[str]] = {}
    for item in items:
        if item.physical_page is None:
            continue
        text = re.sub(r"\s+", " ", (item.text or "").strip())
        if text:
            by_page.setdefault(item.physical_page, []).append(text)

    signals: dict[int, tuple[bool, int, int]] = {}
    for page, texts in by_page.items():
        has_title = any(_TOC_TITLE_RE.fullmatch(text) for text in texts)
        if not has_title:
            has_title = any(
                _TOC_TITLE_RE.fullmatch(texts[index] + texts[index + 1])
                for index in range(len(texts) - 1)
            )
        leader_entries = sum(bool(_TOC_DOT_LEADER_RE.search(text)) for text in texts)
        paired_entries = 0
        for index, text in enumerate(texts[:-1]):
            if not _TOC_ENTRY_START_RE.match(text) or len(text) > 120:
                continue
            # OCR may insert one small fragment between a title and its page
            # number, so accept either of the next two blocks.
            if any(_TOC_PAGE_NUMBER_RE.fullmatch(candidate)
                   for candidate in texts[index + 1:index + 3]):
                paired_entries += 1
        signals[page] = (has_title, leader_entries, paired_entries)

    # Start from explicit evidence, then admit only adjacent continuation
    # pages. Without this adjacency rule, ordinary body pages containing a
    # numbered clause followed by numbered list items look like split TOC
    # entries (the false-positive that this gate is designed to avoid).
    toc_pages = {
        page for page, (has_title, leader_entries, _pairs) in signals.items()
        if has_title or (page <= 20 and leader_entries >= 2)
    }
    changed = True
    while changed:
        changed = False
        for page, (_title, leader_entries, paired_entries) in signals.items():
            if page in toc_pages:
                continue
            if (page - 1 in toc_pages or page + 1 in toc_pages) and (
                    leader_entries >= 1 or paired_entries >= 3):
                toc_pages.add(page)
                changed = True
    return toc_pages


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
            # A numbered body line is not a structural heading merely because
            # OCR placed it in its own box.  Terminal sentence punctuation is
            # decisive; normative verbs are decisive once the trailing text
            # is longer than an ordinary short title.
            if _looks_like_numbered_body(text, node):
                if node.level == 1:
                    continue
                # A numbered clause remains a structural anchor, but its body
                # is not a heading title.  Keeping only the label prevents the
                # clause's first line from being synthesized into breadcrumbs
                # while preserving useful clause-level section_path metadata.
                node = HeadingNode(
                    label=node.label,
                    title=None,
                    level=node.level,
                    confidence=HIGH_CONFIDENCE,
                    source_index=node.source_index,
                )
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


def _looks_like_numbered_body(text: str, node: HeadingNode) -> bool:
    title = node.title or ""
    # OCR paragraph reconstruction deliberately preserves the source line
    # boundaries.  A numbered multi-line block is clause/body content even
    # when it crosses a page before reaching terminal punctuation.
    if "\n" in text:
        return True
    if _BODY_END_RE.search(text):
        return True
    if node.level == 1 and re.search(r"[。；;：:，,]", title):
        return True
    return bool(title and len(title) > 8 and _NORMATIVE_VERB_RE.search(title))


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
        if "." in h.label and all(part.isdigit() for part in h.label.split(".")):
            # A dotted clause/section can inherit only numeric ancestors that
            # are real prefixes of its label.  This prevents a short list item
            # misread as root ("1 …") or a stale sibling ("4.4") from
            # contaminating an explicit path such as 5.10.7 or 4.3.13.
            stack = [ancestor for ancestor in stack if (
                not ancestor.label.replace(".", "").isdigit()
                or h.label == ancestor.label
                or h.label.startswith(ancestor.label + ".")
            )]
        # Pop anything at the same or deeper level so the stack stays a
        # strictly increasing level chain (1 -> 2 -> 3 ...).
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)

    return [{"label": h.label, "title": h.title} for h in stack]
