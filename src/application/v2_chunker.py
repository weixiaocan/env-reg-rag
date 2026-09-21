"""V2 Chunker: project a V2 Canonical Document into retrieval chunks (plan 2.2).

This is the Element-owned upgrade of the V1 ``_text_units`` splitter
(``src/evaluation/evidence_builder.py:137``). V1 was page-scoped and
character-budgeted; V2 is global (cross-page clauses assemble into one chunk),
type-aware (table / formula / figure are first-class atomic units), and
token-budgeted with the real bge tokenizer.

Cutting strategy (plan §2.2 "切分格式 5 步"):

1. Global traversal of ``elements`` in array order (reading order) -- not
   page-scoped, so a clause split across pages becomes one chunk.
2. Per-type projection (``_project_text``): text by role (filtered), table
   expanded to caption + rows, formula atomic, figure caption + references.
3. Structure-aware cutting -- three breakpoints (heading / clause start /
   structured unit) + one merge (adjacent short clauses/paragraphs/list_items
   fill the token budget).
4. Token budget (default 480) + overlap (64) via the bge tokenizer; an overlong
   single clause is split intra-clause with
   ``langchain_text_splitters.RecursiveCharacterTextSplitter`` (token-based
   ``length_function``) -- langchain only does intra-clause recursion, never the
   legal-structure cut.
5. ``section_path`` prefix read straight off each Element (no runtime
   accumulation) -- fixes the V1 83% heading_path loss.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Protocol

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.domain.v2_chunk import (
    DEFAULT_PROJECTION_VERSION,
    ChunkSourceSpan,
    V2Chunk,
    compute_chunk_id,
)

# --------------------------------------------------------------------------- #
# V1 parity constants (copied verbatim from evidence_builder.py:21 so clause
# detection is byte-identical to V1 -- plan §2.2 "务必保留 V1 _CLAUSE_START").
# --------------------------------------------------------------------------- #

_CLAUSE_START = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千0-9]+[章节条款项]|[0-9]+(?:\s*\.\s*[0-9A-Za-z]+){1,4})(?!\s*[~～—-]\s*\d)(?:\s|[^0-9])"
)

# text roles excluded from retrieval projection (V1 _IGNORED_TYPES equivalent;
# design §9.2 requires Chunker to drop these by role).
_IGNORED_ROLES = {"header", "footer", "page_number", "watermark", "unknown"}

# Hard cap = bge-small-zh max_length (512). Every emitted chunk's full text
# (section_path prefix + projected body) stays at or below this so it embeds
# without truncation. ``token_budget`` is the soft body target; the hard cap is
# the real guard.
_HARD_CAP_TOKENS = 512

# Intra-clause recursive splitter separators (plan §2.2 step 4): block, line,
# full-stop, semicolon, fallback char.
_SEPARATORS = ["\n\n", "\n", "。", "；", ""]


class _Tokenizer(Protocol):
    def count(self, text: str) -> int: ...


class _BgeTokenizerWrapper:
    """Lazy bge tokenizer (tokenizer only -- no torch model) for token counting."""

    def __init__(self, tokenizer: Any) -> None:
        self._tok = tokenizer

    def count(self, text: str) -> int:
        return len(
            self._tok.encode(
                text,
                add_special_tokens=True,
                truncation=False,
                verbose=False,
            )
        )


_DEFAULT_TOKENIZER: _Tokenizer | None = None


def _get_default_tokenizer() -> _Tokenizer:
    global _DEFAULT_TOKENIZER
    if _DEFAULT_TOKENIZER is None:
        from transformers import AutoTokenizer

        from src.retrieval.bge_small_zh import BgeSmallZhEmbedder

        tok = AutoTokenizer.from_pretrained(
            BgeSmallZhEmbedder.model_id,
            revision=BgeSmallZhEmbedder.model_revision,
            local_files_only=True,
        )
        _DEFAULT_TOKENIZER = _BgeTokenizerWrapper(tok)
    return _DEFAULT_TOKENIZER


def _resolve_tokenizer(tokenizer: Any) -> _Tokenizer:
    if tokenizer is None:
        return _get_default_tokenizer()
    if hasattr(tokenizer, "count"):
        return tokenizer
    if hasattr(tokenizer, "encode"):
        return _BgeTokenizerWrapper(tokenizer)
    if callable(tokenizer):
        # Ad-hoc: wrap a bare callable into the protocol.
        class _Closure:
            def count(self, text: str) -> int:
                return int(tokenizer(text))
        return _Closure()
    raise TypeError("tokenizer must expose .count(text), .encode(text), or be callable")


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _format_section_path(section_path: list[dict[str, Any]]) -> str:
    """``5 管渠混接调查 > 5.4 浮标法 > 5.4.2 流量计算`` + trailing newline.

    Empty section_path -> empty string (no prefix).
    """
    parts: list[str] = []
    for entry in section_path or []:
        label = entry.get("label") or ""
        title = entry.get("title")
        if title:
            parts.append(f"{label} {title}".strip())
        elif label:
            parts.append(label)
    if not parts:
        return ""
    return " > ".join(parts) + "\n"


def _project_table(content: dict[str, Any]) -> str:
    """caption + rows (cells joined by ``|``); merged cells propagated to the grid."""
    row_count = content["row_count"]
    column_count = content["column_count"]
    grid: list[list[str]] = [[""] * column_count for _ in range(row_count)]
    for cell in content.get("cells") or []:
        r0, c0 = cell["row"], cell["column"]
        rs, cs = cell["row_span"], cell["column_span"]
        text = cell.get("text") or ""
        for r in range(r0, min(r0 + rs, row_count)):
            for c in range(c0, min(c0 + cs, column_count)):
                grid[r][c] = text
    rows = [" | ".join(grid[r]) for r in range(row_count)]
    lines = []
    caption = content.get("caption")
    if caption:
        lines.append(caption.strip())
    lines.extend(rows)
    notes = content.get("notes") or []
    for note in notes:
        if note.get("text"):
            lines.append(note["text"].strip())
    return "\n".join(lines).strip()


def _project_formula(content: dict[str, Any]) -> str:
    number = content.get("formula_number")
    body = content.get("latex") or content.get("recognized_formula") or ""
    context = content.get("context_text")
    lines = []
    header = "[公式" + (f" {number}" if number else "") + "]"
    lines.append(header)
    if body:
        lines.append(body.strip())
    if context:
        lines.append(context.strip())
    return "\n".join(lines).strip()


def _project_figure(content: dict[str, Any]) -> str | None:
    caption = content.get("caption")
    refs = [r.get("text") or "" for r in content.get("references") or []]
    refs = [r.strip() for r in refs if r.strip()]
    if not caption and not refs:
        return None  # design §8.4: no caption/references -> skip
    lines = []
    if caption:
        lines.append(f"[{caption.strip()}]")
    lines.extend(refs)
    return "\n".join(lines).strip()


def _project_text(element: dict[str, Any]) -> str | None:
    """Project one Element to retrieval text. Returns None to skip."""
    etype = element["type"]
    if etype == "text":
        role = element.get("role")
        if role in _IGNORED_ROLES:
            return None
        text = (element.get("content") or {}).get("text") or ""
        text = text.strip()
        return text or None
    if etype == "table":
        return _project_table(element["content"]) or None
    if etype == "formula":
        return _project_formula(element["content"]) or None
    if etype == "figure":
        return _project_figure(element["content"])
    return None


# --------------------------------------------------------------------------- #
# Source-span aggregation
# --------------------------------------------------------------------------- #

def _collect_source_spans(elements: list[dict[str, Any]]) -> list[ChunkSourceSpan]:
    seen: set[tuple[int, tuple[float, ...]]] = set()
    out: list[ChunkSourceSpan] = []
    for e in elements:
        for s in e.get("source_spans") or []:
            page = s["physical_page"]
            bbox = tuple(s["bbox"])
            key = (page, bbox)
            if key in seen:
                continue
            seen.add(key)
            out.append(ChunkSourceSpan(physical_page=page, bbox=list(bbox)))
    return out


def _doc_level_metadata(canonical: dict[str, Any]) -> dict[str, Any]:
    md = canonical.get("metadata") or {}
    standard_number = ""
    for ident in md.get("identifiers") or []:
        if ident.get("scheme") == "standard_number":
            standard_number = ident.get("value") or ""
            break
    return {
        "standard_number": standard_number,
        "document_kind": md.get("document_kind", "unknown"),
        "jurisdictions": list(md.get("jurisdictions") or []),
        "effective_status": md.get("effective_status", "unknown"),
    }


# --------------------------------------------------------------------------- #
# Accumulator
# --------------------------------------------------------------------------- #

class _PendingUnit:
    __slots__ = ("element_id", "text", "section_path")

    def __init__(self, element_id: str, text: str, section_path: list[dict[str, Any]]):
        self.element_id = element_id
        self.text = text
        self.section_path = section_path


class _ChunkBuilder:
    """Accumulates pending text units and emits V2Chunk objects."""

    def __init__(
        self,
        *,
        canonical: dict[str, Any],
        projection_version: str,
        token_budget: int,
        overlap: int,
        count_fn: Callable[[str], int],
        valid_pages: set[int],
    ) -> None:
        self._canonical_id = canonical["canonical_id"]
        self._canonical_content_id = canonical["canonical_content_id"]
        self._projection_version = projection_version
        self._token_budget = token_budget
        self._overlap = overlap
        self._count = count_fn
        self._valid_pages = valid_pages
        self._doc_meta = _doc_level_metadata(canonical)
        self._source_sha = (canonical.get("source") or {}).get("sha256", "")
        self._file_name = (canonical.get("source") or {}).get("file_name", "")
        self._chunks: list[V2Chunk] = []
        self._pending: list[_PendingUnit] = []
        self._prefix_cache: dict[str, str] = {}

    # -- prefix helpers ----------------------------------------------------- #

    def _prefix_for(self, section_path: list[dict[str, Any]]) -> str:
        key = " | ".join((e.get("label") or "") for e in section_path)
        if key not in self._prefix_cache:
            self._prefix_cache[key] = _format_section_path(section_path)
        return self._prefix_cache[key]

    def _prefix_tokens_for(self, section_path: list[dict[str, Any]]) -> int:
        return self._count(self._prefix_for(section_path))

    # -- pending management ------------------------------------------------- #

    def _try_add_to_pending(self, unit: _PendingUnit) -> bool:
        """Return True if appended; False if the solo unit must be split.

        Split triggers (plan §2.2 step 4):
        * a solo clause whose body exceeds ``token_budget`` -> intra-clause split
          (keeps chunks near the budget even when the whole clause fits the hard
          cap, improving retrieval granularity);
        * any text that would breach the 512 hard cap -> must split.
        """
        prefix = self._prefix_for(unit.section_path)
        if self._pending:
            body = "\n".join(u.text for u in self._pending) + "\n" + unit.text
        else:
            body = unit.text
        full = prefix + body
        if self._count(full) > _HARD_CAP_TOKENS:
            if self._pending:
                self.flush()
            return self._append_solo(unit, prefix)
        # Solo clause over budget -> split (plan: 单条 > budget).
        if not self._pending and self._count(unit.text) > self._token_budget:
            return False
        self._pending.append(unit)
        return True

    def _append_solo(self, unit: _PendingUnit, prefix: str) -> bool:
        if self._count(prefix + unit.text) > _HARD_CAP_TOKENS:
            return False
        if self._count(unit.text) > self._token_budget:
            return False
        self._pending.append(unit)
        return True

    def flush(self) -> None:
        if not self._pending:
            return
        units = self._pending
        self._pending = []
        section_path = units[0].section_path
        prefix = self._prefix_for(section_path)
        body = "\n".join(u.text for u in units)
        full = prefix + body
        if self._count(full) <= _HARD_CAP_TOKENS:
            self._emit_text_chunk(units, prefix, body)
        else:
            # Safety net: joined body overlong -> split recursively.
            for sub in self._split_body(prefix, body):
                self._emit_text_chunk(units, prefix, sub)

    def _split_body(self, prefix: str, body: str) -> list[str]:
        prefix_tokens = self._count(prefix)
        # chunk_size = budget (plan: 480), clamped so prefix+sub <= hard cap.
        chunk_size = max(1, min(self._token_budget, _HARD_CAP_TOKENS - prefix_tokens))
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=min(self._overlap, max(1, chunk_size - 1)),
            separators=list(_SEPARATORS),
            length_function=self._count,
        )
        subs = [s for s in splitter.split_text(body) if s.strip()]
        return subs or [body]

    def emit_structured(self, element: dict[str, Any], text: str) -> None:
        """Emit a table/formula/figure as one atomic chunk."""
        self.flush()
        section_path = element.get("section_path") or []
        prefix = self._prefix_for(section_path)
        full = prefix + text
        self._emit_chunk(
            element_ids=[element["element_id"]],
            text=full,
            section_path=section_path,
            elements=[element],
            element_types=[element["type"]],
        )

    def emit_intra_clause_split(self, element: dict[str, Any], text: str) -> None:
        """Split a single overlong clause into sub-chunks (each carries prefix)."""
        self.flush()
        section_path = element.get("section_path") or []
        prefix = self._prefix_for(section_path)
        for sub in self._split_body(prefix, text):
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=prefix + sub,
                section_path=section_path,
                elements=[element],
                element_types=["text"],
            )

    def _emit_text_chunk(
        self,
        units: list[_PendingUnit],
        prefix: str,
        body: str,
    ) -> None:
        full = prefix + body
        elements = [self._element_lookup[u.element_id] for u in units]
        self._emit_chunk(
            element_ids=[u.element_id for u in units],
            text=full,
            section_path=units[0].section_path,
            elements=elements,
            element_types=["text"],
        )

    def _emit_chunk(
        self,
        *,
        element_ids: list[str],
        text: str,
        section_path: list[dict[str, Any]],
        elements: list[dict[str, Any]],
        element_types: list[str],
    ) -> None:
        text = text.strip()
        if not text:
            return
        source_spans = _collect_source_spans(elements)
        physical_pages = sorted({s.physical_page for s in source_spans})
        chunk_id = compute_chunk_id(
            canonical_content_id=self._canonical_content_id,
            element_ids=element_ids,
            projection_version=self._projection_version,
            text=text,
        )
        metadata = dict(self._doc_meta)
        metadata["element_types"] = element_types
        metadata["physical_pages"] = physical_pages
        metadata["section_path"] = [dict(e) for e in section_path]
        metadata["file_name"] = self._file_name
        metadata["source_sha256"] = self._source_sha
        chunk = V2Chunk(
            schema_version="v2.chunk/1.0",
            chunk_id=chunk_id,
            canonical_id=self._canonical_id,
            canonical_content_id=self._canonical_content_id,
            element_ids=element_ids,
            text=text,
            source_spans=source_spans,
            projection_version=self._projection_version,
            metadata=metadata,
        )
        self._chunks.append(chunk)

    def bind_elements(self, elements: list[dict[str, Any]]) -> None:
        self._element_lookup = {e["element_id"]: e for e in elements}

    @property
    def chunks(self) -> list[V2Chunk]:
        self.flush()
        return self._chunks


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def chunk_document(
    canonical: dict[str, Any],
    *,
    projection_version: str = DEFAULT_PROJECTION_VERSION,
    token_budget: int = 480,
    overlap: int = 64,
    tokenizer: Any = None,
) -> list[V2Chunk]:
    """Project a V2 Canonical Document into retrieval chunks.

    Parameters mirror plan §2.2. ``token_budget`` is the soft body target; the
    hard cap of 512 (bge-small-zh max) is always respected. ``tokenizer`` may be
    None (lazy bge tokenizer), an object with ``.count``/``.encode``, or a
    callable ``text -> int`` (for tests).
    """
    if not isinstance(canonical, dict):
        raise TypeError("canonical must be a dict")
    elements = canonical.get("elements") or []
    if not elements:
        return []
    valid_pages = {p["physical_page"] for p in (canonical.get("pages") or [])}
    count_fn = _resolve_tokenizer(tokenizer).count

    builder = _ChunkBuilder(
        canonical=canonical,
        projection_version=projection_version,
        token_budget=token_budget,
        overlap=overlap,
        count_fn=count_fn,
        valid_pages=valid_pages,
    )
    builder.bind_elements(elements)

    for element in elements:
        etype = element.get("type")
        role = element.get("role")

        # Structured units are atomic: flush, then emit standalone.
        if etype in ("table", "formula", "figure"):
            proj = _project_text(element)
            if proj:
                builder.emit_structured(element, proj)
            continue

        if etype == "text":
            # Heading: flush + adopt this element's section_path (no runtime
            # accumulation -- plan §2.2 step 5). Heading text is carried by the
            # section_path prefix of subsequent chunks, so it is not emitted as
            # a standalone chunk (matches V1 heading-as-context behaviour).
            if role == "heading":
                # Flush + adopt this element's section_path as the current
                # context. The heading text itself is NOT emitted as a chunk --
                # it is carried by the section_path prefix of subsequent chunks
                # (matches V1 heading-as-context behaviour; plan §2.2 step 5).
                builder.flush()
                continue
            if role in _IGNORED_ROLES:
                continue
            proj = _project_text(element)
            if not proj:
                continue
            # Breakpoint B: clause start -> flush first.
            is_clause_start = role == "clause" or bool(_CLAUSE_START.match(_normalize(proj)))
            if is_clause_start:
                builder.flush()
            unit = _PendingUnit(element["element_id"], proj, element.get("section_path") or [])
            added = builder._try_add_to_pending(unit)
            if not added:
                # Overlong solo clause -> split intra-clause.
                builder.emit_intra_clause_split(element, proj)
            continue
        # Unknown element type -> skip (defensive).
        continue

    return builder.chunks
