"""Cross-checks between what the text announces and what the corpus holds.

A standard announces its own structure: "按下式计算" is followed by a formula,
"式中" explains the formula just above it, a printed "(4.1.11)" numbers a
formula, "见表 4.1.3" / "如图 3.0.4" point at a table or figure of the same
document. Each announcement without its counterpart is a finding: either the
corpus lost the formula/figure/table, or the reference is legitimately
external and gets an exception with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable

LEAD_IN = re.compile(r"(?<!地)下式|下列公式|如下公式|公式如下|下列各式|按式\s*[\(（]")
WHERE = re.compile(r"^\s*式\s*中")
EQUATION_NUMBER = re.compile(
    r"[\(（]\s*([A-Z]?\s*\d{1,2}(?:\s*[\.．]\s*\d{1,2}){0,3}(?:\s*[-－]\s*\d{1,2})?)\s*[\)）]\s*$")
TOC_LEADER = re.compile(r"(?:…{3,}|\.{4,}|·{4,}|．{4,})")
FIGURE_REF = re.compile(r"(?<![附续])图\s*([A-Z]?\d+(?:\s*[\.．-]\s*\d+){0,3})")
APPENDIX_FIGURE_CAPTION = re.compile(r"附图\s*([A-Z]?\d+(?:\s*[\.．-]\s*\d+){0,3})")
TABLE_REF = re.compile(r"(?<![附续])表\s*([A-Z]?\d+(?:\s*[\.．-]\s*\d+){0,3})")
CAPTION_LINE = re.compile(r"^\s*(?:续\s*)?(?:附\s*)?[图表]\s*[A-Z]?\d")
EXTERNAL_REF = re.compile(r"GB|CJJ|CECS|JGJ|DBJ|QX|标准》|规范》|规程》|原“规范”|原规范")

LEAD_IN_WINDOW = 6
_FURNITURE_ROLES = {"page_number", "header", "footer", "watermark"}
WHERE_WINDOW = 3
NUMBER_Y_TOLERANCE = 20.0


@dataclass
class Finding:
    check: str
    physical_page: int | None
    element_id: str | None
    text: str
    key: str


def _key(check: str, sha: str, page: Any, text: str) -> str:
    return hashlib.sha256(f"{check}|{sha}|{page}|{text}".encode("utf-8")).hexdigest()[:16]


def _text(element: dict[str, Any]) -> str:
    return str((element.get("content") or {}).get("text") or "") if element.get("type") == "text" else ""


def _flat(text: str) -> str:
    """Text without layout whitespace: "附 图2" and "地 下式" read as printed."""
    return re.sub(r"\s+", "", text)


def _page(element: dict[str, Any]) -> int | None:
    spans = element.get("source_spans") or []
    return int(spans[0]["physical_page"]) if spans and spans[0].get("physical_page") is not None else None


def _norm_id(value: str) -> str:
    return re.sub(r"\.+", ".", re.sub(r"[\s．]", ".", value)).strip(".")


def check_lead_ins(sha: str, elements: list[dict[str, Any]]) -> list[Finding]:
    body = [e for e in elements if e.get("role") not in _FURNITURE_ROLES]
    out = []
    for i, e in enumerate(body):
        text = _text(e)
        if not text:
            continue
        if LEAD_IN.search(_flat(text)) and not any(
                x.get("type") == "formula" for x in body[i + 1:i + 1 + LEAD_IN_WINDOW]):
            out.append(Finding("lead_in_without_formula", _page(e), e.get("element_id"),
                               text[-60:], _key("lead_in", sha, _page(e), text)))
        if WHERE.match(text) and not any(
                x.get("type") == "formula" for x in body[max(0, i - WHERE_WINDOW):i]):
            out.append(Finding("where_without_formula", _page(e), e.get("element_id"),
                               text[:60], _key("where", sha, _page(e), text)))
    return out


def check_equation_numbers(
    sha: str, elements: list[dict[str, Any]], page_lines: dict[int, list[dict[str, Any]]],
    page_widths: dict[int, float], toc_pages: set[int] = frozenset(),
) -> list[Finding]:
    """Every printed right-hand "(4.1.11)" on a body page sits beside a formula."""
    boxes: dict[int, list[list[float]]] = {}
    for e in elements:
        if e.get("type") != "formula":
            continue
        for s in e.get("source_spans") or []:
            if s.get("bbox"):
                boxes.setdefault(int(s["physical_page"]), []).append(s["bbox"])
    out = []
    for page, lines in page_lines.items():
        if page in toc_pages or sum(
                1 for l in lines if TOC_LEADER.search(str(l.get("text") or ""))) >= 3:
            continue
        width = page_widths.get(page) or max((l["bbox"][2] for l in lines), default=1.0)
        for line in lines:
            text = str(line.get("text") or "").strip()
            m = EQUATION_NUMBER.search(text)
            if not m or line["bbox"][0] < width * 0.5 or TOC_LEADER.search(text):
                continue
            if re.search(r"[一-鿿]{2,}", text[:m.start()]):
                continue
            yc = (line["bbox"][1] + line["bbox"][3]) / 2
            if any(b[1] - NUMBER_Y_TOLERANCE <= yc <= b[3] + NUMBER_Y_TOLERANCE
                   for b in boxes.get(page, [])):
                continue
            out.append(Finding("equation_number_without_formula", page, None, text,
                               _key("eqnum", sha, page, text)))
    return out


def check_references(sha: str, elements: list[dict[str, Any]]) -> list[Finding]:
    """Same-document "图 x" / "表 x" references resolve to a figure or table."""
    figure_ids: set[str] = set()
    table_ids: set[str] = set()
    caption_text = []
    for e in elements:
        content = e.get("content") or {}
        if e.get("type") in {"figure", "table"} and content.get("caption"):
            caption_text.append(str(content["caption"]))
        text = _text(e)
        # A title kept as text: a caption block, or any line that opens with
        # the figure/table number ("表4.1.5 内涝防治设计重现期下的...").
        if e.get("role") == "caption":
            caption_text.append(_flat(text)[:60])
        caption_text.extend(_flat(line)[:60] for line in text.splitlines()
                            if CAPTION_LINE.match(line))
    for cap in caption_text:
        for m in FIGURE_REF.finditer(cap):
            figure_ids.add(_norm_id(m.group(1)))
        for m in APPENDIX_FIGURE_CAPTION.finditer(cap):
            figure_ids.add(_norm_id(m.group(1)))
        for m in TABLE_REF.finditer(cap):
            table_ids.add(_norm_id(m.group(1)))
    out = []
    seen: set[tuple[str, str]] = set()
    for e in elements:
        if e.get("type") != "text" or e.get("role") in {"caption", "toc_navigation_only"}:
            continue
        text = _flat(_text(e))
        for kind, pattern, known in (("figure", FIGURE_REF, figure_ids), ("table", TABLE_REF, table_ids)):
            for m in pattern.finditer(text):
                ref = _norm_id(m.group(1))
                window = text[max(0, m.start() - 30):m.start()]
                if ref in known or EXTERNAL_REF.search(window) or (kind, ref) in seen:
                    continue
                seen.add((kind, ref))
                out.append(Finding(f"{kind}_reference_unresolved", _page(e), e.get("element_id"),
                                   f"{kind} {ref}: {text[max(0, m.start() - 20):m.end() + 10]}",
                                   _key(f"{kind}ref", sha, ref, "")))
    return out


def apply_exceptions(findings: Iterable[Finding], exceptions: dict[str, str]) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    open_, excepted = [], []
    for f in findings:
        if f.key in exceptions:
            excepted.append((f, exceptions[f.key]))
        else:
            open_.append(f)
    return open_, excepted
