"""Score chunk output against the human-approved M2 anchor benchmark.

The benchmark (``data/registry/m2-anchor-review-v0.json``) holds 30 anchors a
human checked against the physical PDF page: 20 ``text_or_clause`` anchors
(verbatim truth + ``structure_path``) and 10 ``table_or_numeric`` anchors
(normalised value summaries). This module grades what retrieval actually sees
-- chunks -- so a pipeline is judged on its output, not on its own reports.

Grading rules (acceptance criterion A1 of the middle-layer v3 spec):

* text: the normalised truth occurs inside the body of one chunk whose pages
  intersect ``pdf_page +- 1``; ``structure`` additionally requires that the
  chunk's last section label equals the anchor's last structure label (when
  the anchor's last node carries a label at all).
* table: every number in the truth occurs in the normalised text of the
  chunks on ``pdf_page +- 1``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

_STRIP = re.compile(r"[\s，,。．.;；:：、()（）\[\]【】\-—–~～·•|“”\"'‘’《》<>]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_LABEL = re.compile(
    r"^(?:\d+(?:\.\d+)*|第[一二三四五六七八九十百\d]+[章节条]|[一二三四五六七八九十]+、"
    r"|[（(][一二三四五六七八九十\d]+[)）]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)"
)


def normalize(text: str | None) -> str:
    return _STRIP.sub("", unicodedata.normalize("NFKC", text or ""))


def normalize_label(label: str | None) -> str:
    """Section labels keep their dots ("2.1" != "21"); only width/space differ."""
    return re.sub(r"[\s、]", "", unicodedata.normalize("NFKC", label or ""))


def structure_label(node: str | None) -> str | None:
    """Leading numbering label of one structure node, normalised; None if absent."""
    match = _LABEL.match(unicodedata.normalize("NFKC", (node or "").strip()))
    return normalize_label(match.group(0)) if match else None


@dataclass(frozen=True)
class ExamChunk:
    """The minimum a chunk must expose to be graded."""

    document: str
    pages: frozenset[int]
    section_labels: tuple[str, ...]
    body: str


@dataclass
class AnchorResult:
    anchor_id: str
    anchor_type: str
    document: str
    pdf_page: int
    text_pass: bool
    structure_pass: bool | None  # None = anchor has no labelled structure node
    matched_section: str | None = None
    detail: str = ""


@dataclass
class ExamReport:
    results: list[AnchorResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for kind in ("text_or_clause", "table_or_numeric"):
            rows = [r for r in self.results if r.anchor_type == kind]
            out[kind] = {"total": len(rows), "text_pass": sum(r.text_pass for r in rows)}
            graded = [r for r in rows if r.structure_pass is not None]
            if kind == "text_or_clause":
                out[kind]["structure_graded"] = len(graded)
                out[kind]["structure_pass"] = sum(bool(r.structure_pass) for r in graded)
        out["failed"] = [r.anchor_id for r in self.results
                         if not r.text_pass or r.structure_pass is False]
        return out


def _same_document(anchor_name: str, chunk_document: str) -> bool:
    a, c = normalize(anchor_name), normalize(chunk_document)
    return bool(a) and bool(c) and (a == c or a in c or c in a)


def grade(anchors: Iterable[dict[str, Any]], chunks: Iterable[ExamChunk]) -> ExamReport:
    chunk_list = list(chunks)
    report = ExamReport()
    for anchor in anchors:
        page = int(anchor["pdf_page"])
        window = {page - 1, page, page + 1}
        doc_chunks = [c for c in chunk_list if _same_document(anchor["file_name"], c.document)]
        near = [c for c in doc_chunks if c.pages & window]
        truth = str(anchor.get("proposed_truth") or "")
        nodes = [n for n in str(anchor.get("structure_path") or "").split(">") if n.strip()]
        want_label = structure_label(nodes[-1]) if nodes else None
        result = AnchorResult(anchor["anchor_id"], anchor["anchor_type"], anchor["file_name"],
                              page, False, None)
        if not doc_chunks:
            result.detail = "document not present in chunk set"
        elif anchor["anchor_type"] == "table_or_numeric":
            numbers = _NUMBER.findall(unicodedata.normalize("NFKC", truth))
            haystack = "".join(normalize(c.body) for c in near)
            missing = [n for n in numbers if normalize(n) not in haystack]
            result.text_pass = bool(numbers) and not missing
            result.detail = f"missing numbers: {missing}" if missing else ""
        else:
            needle = normalize(truth)
            hits = [c for c in near if needle and needle in normalize(c.body)]
            result.text_pass = bool(hits)
            if want_label is not None:
                result.structure_pass = any(
                    c.section_labels and c.section_labels[-1] == want_label for c in hits)
                if hits:
                    result.matched_section = " > ".join(hits[0].section_labels)
            if not hits:
                result.detail = ("truth absent near page; present elsewhere in document"
                                 if any(needle in normalize(c.body) for c in doc_chunks)
                                 else "truth absent from document chunks")
        report.results.append(result)
    return report


def chunks_from_v2(document_name: str, v2_chunks: Iterable[Any]) -> list[ExamChunk]:
    """Adapt ``V2Chunk`` objects (body = text minus the section prefix)."""
    from src.application.v2_chunker import _format_section_path

    out = []
    for chunk in v2_chunks:
        path = chunk.metadata.get("section_path") or []
        prefix = _format_section_path(path)
        body = chunk.text[len(prefix):] if prefix and chunk.text.startswith(prefix) else chunk.text
        out.append(ExamChunk(
            document=document_name,
            pages=frozenset(int(s.physical_page) for s in chunk.source_spans),
            section_labels=tuple(normalize_label(e.get("label")) for e in path),
            body=body,
        ))
    return out
