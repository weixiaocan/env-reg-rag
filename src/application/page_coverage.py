"""Page coverage: is everything printed on a page somewhere in the corpus?

Every other check in the pipeline looks at what was extracted. This one looks
at what was printed. For each page it takes an independent reading of the
page -- the independent OCR run for scanned pages, the PDF text layer for
the rest -- and asks, line by line, whether the canonical document holds that
text. A line is accounted for when

* its characters are found in the page's canonical text (text elements,
  formula LaTeX and numbers, table captions and cells, figure captions), or
* it lies inside an emitted formula, table or figure element, or
* it is page furniture (page number, recurring header/footer, watermark) or
  sits on a table-of-contents page, or
* an owner/agent exception names it with a reason.

Anything else is ``missing``: printed on the page, absent from the corpus.
The publish gate requires zero missing lines.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import re
import unicodedata
from typing import Any, Iterable

from src.ingestion.page_furniture import classify_page_furniture

COVERED_THRESHOLD = 0.6
GEOMETRY_THRESHOLD = 0.6
MIN_LINE_CHARS = 2

_LATEX_COMMAND = re.compile(r"\\(?:mathrm|mathbf|mathit|operatorname|text|left|right|big|Big|bigg|Bigg)\b")
_LATEX_WORDS = {
    r"\cdot": "·", r"\times": "×", r"\leq": "≤", r"\geq": "≥", r"\le": "≤", r"\ge": "≥",
    r"\pm": "±", r"\sum": "Σ", r"\Delta": "Δ", r"\alpha": "α", r"\beta": "β",
    r"\gamma": "γ", r"\lambda": "λ", r"\mu": "μ", r"\rho": "ρ", r"\Psi": "Ψ",
    r"\psi": "ψ", r"\phi": "φ", r"\varphi": "φ", r"\theta": "θ", r"\eta": "η",
    r"\tau": "τ", r"\omega": "ω", r"\sigma": "σ", r"\pi": "π", r"\%": "%",
    r"\sqrt": "√", r"\infty": "∞", r"\approx": "≈", r"\sim": "~",
}
_DROP = re.compile(r"[\s{}_^\\$·•．。，,、；;：:（）()\[\]【】<>《》\"'“”‘’!！?？…\-—–－~～|]")
_TOC_LEADER = re.compile(r"(?:…{3,}|\.{4,}|·{4,}|．{4,})\s*[\(（]?\s*\d+\s*[\)）]?\s*$")


def normalize(text: str) -> str:
    """Compare text the way a reader would: ignore layout, width and markup."""
    value = unicodedata.normalize("NFKC", text or "")
    for word, symbol in _LATEX_WORDS.items():
        value = value.replace(word, symbol)
    value = _LATEX_COMMAND.sub("", value)
    value = re.sub(r"\\[A-Za-z]+", "", value)
    value = _DROP.sub("", value)
    return value.lower()


def bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[i:i + 2] for i in range(len(value) - 1)}


def line_coverage(line_norm: str, page_bigrams: set[str]) -> float:
    grams = bigrams(line_norm)
    if not grams:
        return 1.0
    return sum(1 for g in grams if g in page_bigrams) / len(grams)


def element_texts(element: dict[str, Any]) -> list[str]:
    content = element.get("content") or {}
    kind = element.get("type")
    out: list[str] = []
    if kind == "text":
        out.append(str(content.get("text") or ""))
    elif kind == "formula":
        for key in ("latex", "recognized_formula", "formula_number", "context_text"):
            if content.get(key):
                out.append(str(content[key]))
    elif kind == "table":
        if content.get("caption"):
            out.append(str(content["caption"]))
        for cell in content.get("cells") or []:
            out.append(str(cell.get("text") or ""))
    elif kind == "figure":
        if content.get("caption"):
            out.append(str(content["caption"]))
    return [t for t in out if t]


def toc_pages(canonical: dict[str, Any]) -> set[int]:
    """Pages the assembly classified as table of contents."""
    pages = set()
    for element in canonical.get("elements") or []:
        if element.get("role") == "toc_navigation_only":
            for span in element.get("source_spans") or []:
                if span.get("physical_page") is not None:
                    pages.add(int(span["physical_page"]))
    return pages


@dataclass
class PageReference:
    grams: set[str] = field(default_factory=set)
    structured_boxes: list[tuple[str, list[float]]] = field(default_factory=list)
    numbered_formulas: list[tuple[str, list[float]]] = field(default_factory=list)


def page_references(canonical: dict[str, Any]) -> dict[int, PageReference]:
    pages: dict[int, PageReference] = {}
    for element in canonical.get("elements") or []:
        texts = element_texts(element)
        grams: set[str] = set()
        for text in texts:
            grams |= bigrams(normalize(text))
        for span in element.get("source_spans") or []:
            page = span.get("physical_page")
            if page is None:
                continue
            ref = pages.setdefault(int(page), PageReference())
            ref.grams |= grams
            bbox = span.get("bbox")
            if element.get("type") in {"formula", "table", "figure"} and _valid(bbox):
                ref.structured_boxes.append((element["type"], [float(v) for v in bbox]))
                number = (element.get("content") or {}).get("formula_number")
                if element["type"] == "formula" and number:
                    ref.numbered_formulas.append((str(number), [float(v) for v in bbox]))
    return pages


def _valid(bbox: Any) -> bool:
    return isinstance(bbox, (list, tuple)) and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]


def _containment(outer: list[float], inner: list[float]) -> float:
    ix = max(0.0, min(outer[2], inner[2]) - max(outer[0], inner[0]))
    iy = max(0.0, min(outer[3], inner[3]) - max(outer[1], inner[1]))
    area = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return ix * iy / area


def _overlap(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _alternate_covered(bbox: list[float], alternates: list[list[dict[str, Any]]],
                       grams: set[str]) -> bool:
    for reading in alternates:
        for line in reading:
            other = [float(v) for v in line["bbox"]]
            if max(_overlap(bbox, other), _containment(other, bbox)) < 0.5:
                continue
            norm = normalize(str(line.get("text") or ""))
            if len(norm) >= MIN_LINE_CHARS and line_coverage(norm, grams) >= COVERED_THRESHOLD:
                return True
    return False


_FORMULA_NUMBER_LINE = re.compile(r"^[.·…\s]*[（(]\s*([A-Z]?\d+(?:[.．]\d+)*)\s*[）)]\s*$")


def _numbered_formula_covered(text: str, bbox: list[float], reference: PageReference) -> bool:
    match = _FORMULA_NUMBER_LINE.fullmatch(text)
    if not match:
        return False
    center_y = (bbox[1] + bbox[3]) / 2
    return any(number == match.group(1) and box[1] - 20 <= center_y <= box[3] + 20
               for number, box in reference.numbered_formulas)


_TITLE_LINE = re.compile(r"^\s*(?:续\s*)?(?:附\s*)?[图表]\s*[A-Z]?\s*\d")


def _geometry_host(bbox: list[float], text: str, reference: PageReference) -> str | None:
    """The formula/table/figure element whose box holds this line, if any.

    Text drawn inside a figure is part of the image, but a figure or table
    title printed there (a rotated page prints it along the drawing) is
    corpus text and must be found as text.
    """
    for kind, box in reference.structured_boxes:
        inside = _containment(box, bbox) >= GEOMETRY_THRESHOLD
        # An OCR box around a short symbol ("t0", "V1") is larger than the
        # formula box it reads: the formula lying inside the line also counts.
        holds = (kind == "formula" and len(normalize(text)) <= 6
                 and _containment(bbox, box) >= GEOMETRY_THRESHOLD)
        if not (inside or holds):
            continue
        if kind == "figure" and _TITLE_LINE.match(re.sub(r"\s+", "", text)):
            continue
        return kind
    return None


def line_key(sha: str, page: int, text: str) -> str:
    raw = f"{sha}|{page}|{normalize(text)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class LineVerdict:
    page: int
    text: str
    bbox: list[float]
    status: str  # covered | geometry | furniture | toc | trivial | excepted | missing
    coverage: float
    rule: str | None = None
    key: str = ""


def audit_page(
    *, sha: str, page: int, lines: Iterable[dict[str, Any]], reference: PageReference | None,
    page_width: float, page_height: float, watermarks: Iterable[str] = (),
    recurring: dict[tuple[str, str], set[int]] | None = None,
    exceptions: dict[str, str] | None = None, toc_page: bool = False,
    alternates: list[list[dict[str, Any]]] | None = None,
) -> list[LineVerdict]:
    """Classify each line; ``alternates`` are other readings of the same page.

    An OCR misread of a line the corpus holds is not a loss: when another
    reading of the same spot (a second OCR pass, the PDF text layer) is found
    in the corpus, the line counts as covered.
    """
    reference = reference or PageReference()
    lines = [l for l in lines if str(l.get("text") or "").strip()]
    toc_page = toc_page or sum(1 for l in lines if _TOC_LEADER.search(str(l["text"]))) >= 3
    verdicts: list[LineVerdict] = []
    for line in lines:
        text = str(line["text"]).strip()
        bbox = [float(v) for v in line["bbox"]]
        norm = normalize(text)
        key = line_key(sha, page, text)
        cov = line_coverage(norm, reference.grams)
        if len(norm) < MIN_LINE_CHARS:
            status, rule = "trivial", None
        elif cov >= COVERED_THRESHOLD:
            status, rule = "covered", None
        elif _numbered_formula_covered(text, bbox, reference):
            status, rule = "covered", "numbered_formula"
        elif _alternate_covered(bbox, alternates or [], reference.grams):
            status, rule = "covered", "alternate_reading"
        elif (inside := _geometry_host(bbox, text, reference)) is not None:
            status, rule = "geometry", f"inside_{inside}"
        else:
            furniture = classify_page_furniture(
                model_label="text", text=text, bbox=bbox, page_height=page_height,
                page_width=page_width, watermarks=watermarks, recurring=recurring,
                physical_page=page)
            if furniture is not None:
                status, rule = "furniture", furniture.rule_id
            elif toc_page:
                status, rule = "toc", "toc_page"
            elif exceptions and key in exceptions:
                status, rule = "excepted", exceptions[key]
            else:
                status, rule = "missing", None
        verdicts.append(LineVerdict(page, text, bbox, status, round(cov, 3), rule, key))
    return verdicts


def recurring_lines(pages: dict[int, tuple[list[dict[str, Any]], float]], *,
                    min_pages: int = 3, band_ratio: float = 0.12) -> dict[tuple[str, str], set[int]]:
    """Short lines repeated in the same top/bottom band across pages."""
    seen: dict[tuple[str, str], set[int]] = {}
    for page, (lines, height) in pages.items():
        for line in lines:
            bbox = line["bbox"]
            norm = re.sub(r"\s+", "", str(line.get("text") or ""))
            if not norm or len(norm) > 80 or height <= 0:
                continue
            band = ("header" if bbox[1] <= height * band_ratio
                    else "footer" if bbox[3] >= height * (1 - band_ratio) else None)
            if band:
                seen.setdefault((band, norm), set()).add(page)
    return {k: v for k, v in seen.items() if len(v) >= min_pages}


def summarize(verdicts: Iterable[LineVerdict]) -> dict[str, int]:
    return dict(Counter(v.status for v in verdicts))
