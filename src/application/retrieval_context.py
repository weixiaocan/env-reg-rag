"""Index-only context for structured chunks (retrieval projection).

Tables and formulas are atomic chunks: their own text is a grid of cells or
a LaTeX body, and the sentence that says what they are ("降雨分为7个等级，具体
划分见表1。单位为毫米") lives in a neighbouring text chunk.  Embedding and BM25
on the bare grid miss natural questions such as "24小时降雨量多少毫米算暴雨".

This module derives, per chunk, the text that is INDEXED (dense embedding
and BM25).  It never changes ``chunk.text`` -- the evidence shown to the
model and cited in answers stays the chunk itself, so policies, provenance
and chunk identity are untouched.  The lead-in is the nearest preceding
prose under the same heading (plus a short note right after), mirroring
``QdrantContextExpander`` at query time.

BM25 additionally sees the document title, so a question naming a standard
("GB 50014 中…") reaches its chunks; the dense text does not, because the
title diluted short chunks' embeddings for unnamed questions.

Figures are deliberately excluded: a figure chunk is mostly its caption, and
prepending prose drowned the caption in the 2026-10-13 experiment.
"""

from __future__ import annotations

from typing import Any

# Part of the build identity: changing what gets indexed must mint a new
# staging collection.
# /3: one document card per document (registered facts + cover text);
# headings OCR-split one character per line end the cover pages, and a
# contents page before them stays navigation (/2 was an unpublished build).
RETRIEVAL_CONTEXT_VERSION = "structured-lead-in+bm25-title+document-card/3"

_CONTEXT_TYPES = {"table", "formula"}
_LEAD_IN_MAX_CHARS = 200
_LOOKBACK_ELEMENTS = 6
_NOTE_MAX_CHARS = 40


def _section_key(element: dict[str, Any]) -> list[Any]:
    return [s.get("label") or s.get("title") for s in element.get("section_path") or []]


def _prose(element: dict[str, Any]) -> str:
    if element.get("type") != "text" or element.get("role") == "heading":
        return ""
    return ((element.get("content") or {}).get("text") or "").strip()


def lead_in(elements: list[dict[str, Any]], position: int) -> str:
    """Prose framing ``elements[position]``: nearest preceding paragraphs
    under the same heading (up to ~200 chars) and a short trailing note."""
    target = elements[position]
    section = _section_key(target)
    before: list[str] = []
    total = 0
    for element in reversed(elements[max(0, position - _LOOKBACK_ELEMENTS):position]):
        if element.get("type") in _CONTEXT_TYPES | {"figure"}:
            break
        text = _prose(element)
        if not text or _section_key(element) != section:
            continue
        before.insert(0, text)
        total += len(text)
        if total >= _LEAD_IN_MAX_CHARS:
            break
    after: list[str] = []
    for element in elements[position + 1:position + 3]:
        text = _prose(element)
        if not text or len(text) > _NOTE_MAX_CHARS or _section_key(element) != section:
            break
        after.append(text)
    return "\n".join(before + after)[-(_LEAD_IN_MAX_CHARS + _NOTE_MAX_CHARS):]


def retrieval_texts(chunks: list[Any], canonical: dict[str, Any]) -> list[str]:
    """Indexed text per chunk (same order): lead-in + chunk text for chunks
    carrying a table or formula, the chunk text unchanged otherwise."""
    elements = canonical.get("elements") or []
    position = {e["element_id"]: i for i, e in enumerate(elements)}
    out = []
    for chunk in chunks:
        anchors = [
            position[eid] for eid in chunk.element_ids
            if eid in position and elements[position[eid]].get("type") in _CONTEXT_TYPES
        ]
        context = lead_in(elements, min(anchors)) if anchors else ""
        out.append(f"{context}\n{chunk.text}" if context else chunk.text)
    return out


def document_title(canonical: dict[str, Any]) -> str:
    metadata = canonical.get("metadata") or {}
    return (metadata.get("title") or (canonical.get("source") or {}).get("file_name") or "").strip()


def index_texts(
    chunks: list[Any], canonicals: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(dense_texts, sparse_texts) for chunks; ``canonicals`` maps
    ``canonical_id`` to the canonical document the chunk was projected from."""
    groups: dict[str, list[int]] = {}
    for i, chunk in enumerate(chunks):
        groups.setdefault(chunk.canonical_id, []).append(i)
    dense = [""] * len(chunks)
    sparse = [""] * len(chunks)
    for canonical_id, members in groups.items():
        canonical = canonicals[canonical_id]
        texts = retrieval_texts([chunks[i] for i in members], canonical)
        title = document_title(canonical)
        for i, text in zip(members, texts):
            dense[i] = text
            sparse[i] = f"{title}\n{text}" if title else text
    return dense, sparse
