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
   single clause is split intra-clause with an offset-exact window packer
   (recursive separator ladder + exact char ranges -- round 5), so every
   sub-chunk can declare the source range it carries.
5. ``section_path`` prefix read straight off each Element (no runtime
   accumulation) -- fixes the V1 83% heading_path loss.
"""

from __future__ import annotations

from src.application.evidence_policy import LOCATOR, REASON_ANCHOR_OVERFLOW, chunk_usage_policy

import re
from typing import Any, Callable, Protocol

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.domain.v2_chunk import (
    DEFAULT_PROJECTION_VERSION,
    ChunkSourceSpan,
    V2Chunk,
    compute_chunk_id,
)

# T09: projection version for documents that carry the formula/explanation
# association semantics (``formula_context_ledger`` marker). The marker is
# audit-only at the document level, so legacy artifacts never select this
# version -- their re-projection stays byte-identical (publish-gate compat).
# Round 5 ("-2"): occurrence-aware chunk identity -- the declared
# ``source_occurrence`` ranges are folded into the chunk id hash, so
# byte-identical texts at different source positions keep deterministic
# distinct ids. The pre-round-5 string ("chunk-text/v2-formula-binding")
# pinned the OLD id space and must never label the new one.
FORMULA_BINDING_PROJECTION_VERSION = "chunk-text/v2-formula-binding-2"

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
    authorities = md.get("issuing_authorities") or []
    source = canonical.get("source") or {}
    return {
        "standard_number": standard_number,
        "document_kind": md.get("document_kind", "unknown"),
        "jurisdictions": list(md.get("jurisdictions") or []),
        "effective_status": md.get("effective_status", "unknown"),
        # Evidence/document fields consumed by the retrieval + UI layer.
        # primary_evidence_id and source_regions are set per-chunk in _emit_chunk
        # (they depend on chunk_id / are empty by default).
        "document_version_id": (source.get("sha256") or "").lower(),
        # usage_policy is NOT set at the document level. It is projected
        # per-chunk from the source elements' quality/usage via the
        # "strictest source wins" rule (evidence_policy.chunk_usage_policy) so
        # that structured content defaults to locator-only (D3) and any
        # needs_review/quarantined/missing source can never become answer
        # evidence (D2). See _emit_chunk.
        "source_authority": authorities[0] if authorities else "",
        "source_uri": source.get("source_uri") or "",
        "publication_date": md.get("publication_date") or "",
        "effective_from": md.get("effective_from") or "",
        "effective_to": md.get("effective_to") or "",
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
        # T09 closure round 4: chunk_id -> emitted projection fingerprint,
        # feeding the deterministic identical-projection dedupe in
        # _emit_chunk (the single choke point, like the token gate).
        self._emitted: dict[str, tuple] = {}
        # Round 5: occurrence-aware chunk identity (metadata
        # ``source_occurrence`` + occurrence in the id hash) is part of the
        # formula-binding projection semantics ONLY. Legacy projections keep
        # the pre-round-5 identity byte-for-byte, so re-projecting a
        # published legacy corpus for verification never changes its chunk
        # ids (R7 / publish-gate compatibility).
        self._declare_occurrence = (projection_version
                                    == FORMULA_BINDING_PROJECTION_VERSION)

    # -- prefix helpers ----------------------------------------------------- #

    def _prefix_for(self, section_path: list[dict[str, Any]]) -> str:
        key = " | ".join((e.get("label") or "") for e in section_path)
        if key not in self._prefix_cache:
            self._prefix_cache[key] = _format_section_path(section_path)
        return self._prefix_cache[key]

    def _prefix_tokens_for(self, section_path: list[dict[str, Any]]) -> int:
        return self._count(self._prefix_for(section_path))

    def _cap_safe_prefix(self, prefix: str) -> str:
        """Drop a section prefix that can never host body text under the hard
        cap (the section path lives in the chunk metadata anyway)."""
        if self._count(prefix) >= _HARD_CAP_TOKENS:
            return ""
        return prefix

    def _formula_anchor_parts(self, element: dict[str, Any]) -> tuple[str, str]:
        """(prefix, anchor_body) for a formula, with the prefix-drop rule
        applied: a long section prefix is dropped, never the latex. Shared by
        the emission path and the exclusion pre-pass so they can never
        diverge."""
        content = element.get("content") or {}
        number = content.get("formula_number")
        body = content.get("latex") or content.get("recognized_formula") or ""
        header = "[公式" + (f" {number}" if number else "") + "]"
        anchor_body = header + ("\n" + body.strip() if body else "")
        prefix = self._prefix_for(element.get("section_path") or [])
        if (self._count(prefix + anchor_body) > _HARD_CAP_TOKENS
                and self._count(anchor_body) <= _HARD_CAP_TOKENS):
            return "", anchor_body
        return prefix, anchor_body

    def _formula_anchor_view(self, element: dict[str, Any]) -> dict[str, Any]:
        """Formula element restricted to its PRIMARY spans.

        Per-shard source spans may only come from the formula body itself and
        the shard's ACTUALLY-carried explanation elements -- never the
        walk-derived context spans, which point at explanation elements
        carried by OTHER shards or by independent text chunks (T09 closure
        round 3)."""
        view = dict(element)
        view["source_spans"] = [s for s in element.get("source_spans") or []
                                if s.get("role") == "primary"]
        return view

    def _emit_formula_locator(
        self,
        element: dict[str, Any],
        section_path: list[dict[str, Any]],
    ) -> None:
        """Anchor overflow: the chunk stream NEVER carries an over-limit
        chunk. Emit only a SHORT locator record naming the canonical record
        that holds the complete latex; the explanation keeps its independent
        chunks (the exclusion pre-pass does not exclude it for this formula)."""
        content = element.get("content") or {}
        number = content.get("formula_number")
        header = "[公式" + (f" {number}" if number else "") + "]"
        text = (header + "\n"
                + f"（锚点超出{_HARD_CAP_TOKENS} token上限，完整公式本体见 Canonical "
                  f"{self._canonical_id}）")
        self._emit_chunk(
            element_ids=[element["element_id"]],
            text=text,
            section_path=section_path,
            elements=[self._formula_anchor_view(element)],
            element_types=["formula"],
            extra_reasons=[REASON_ANCHOR_OVERFLOW],
        )

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
        prefix = self._cap_safe_prefix(self._prefix_for(section_path))
        body = "\n".join(u.text for u in units)
        full = prefix + body
        if self._count(full) <= _HARD_CAP_TOKENS:
            occurrence = [
                {"element_id": u.element_id, "start": 0, "end": len(u.text)}
                for u in units
            ] if self._declare_occurrence else None
            self._emit_text_chunk(units, prefix, body, occurrence=occurrence)
        elif self._declare_occurrence:
            # Safety net: joined body overlong -> split into offset-exact
            # windows; each window's element-relative ranges follow from the
            # units' body offsets by exact arithmetic (never find-heuristics).
            bounds: list[tuple[int, int]] = []
            off = 0
            for u in units:
                bounds.append((off, off + len(u.text)))
                off += len(u.text) + 1  # the "\n" joiner
            for sub, ws, we in self._split_windows(body, prefix_tokens=0):
                occurrence = []
                for (b0, b1), u in zip(bounds, units):
                    s, e = max(ws, b0), min(we, b1)
                    if s < e:
                        occurrence.append({
                            "element_id": u.element_id,
                            "start": s - b0,
                            "end": e - b0,
                        })
                self._emit_text_chunk(units, prefix, sub,
                                      occurrence=occurrence)
        else:
            # Legacy projection: verbatim pre-round-5 split, no occurrence.
            for sub in self._split_body(prefix, body):
                self._emit_text_chunk(units, prefix, sub)

    def _split_body(self, prefix: str, body: str, *, joiner: str = "") -> list[str]:
        """Legacy splitter path (default projection, pre-round-5 semantics).

        Kept VERBATIM so re-projecting a published legacy corpus reproduces
        its chunk texts and ids byte-for-byte (publish_gate re-chunks every
        canonical; a legacy id drift would fail the gate on an immutable
        corpus). The formula-binding projection uses the offset-exact
        :meth:`_split_windows` instead.
        """
        prefix_tokens = self._count(prefix)
        if joiner and body:
            prefix_tokens += self._count(joiner)
        # chunk_size = budget (plan: 480), clamped so prefix+sub <= hard cap
        # (the ``joiner`` accounts for the separator the caller re-inserts).
        chunk_size = max(1, min(self._token_budget, _HARD_CAP_TOKENS - prefix_tokens))
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=min(self._overlap, max(1, chunk_size - 1)),
            separators=list(_SEPARATORS),
            length_function=self._count,
        )
        subs = [s for s in splitter.split_text(body) if s.strip()]
        return subs or [body]

    def _split_windows(
        self, body: str, *, prefix_tokens: int = 0
    ) -> list[tuple[str, int, int]]:
        """Offset-exact splitter for the formula-binding projection (round 5).

        Returns ``(sub_text, start, end)`` windows where ``start``/``end`` are
        EXACT character ranges within ``body`` (callers fold the prefix/joiner
        they re-insert into ``prefix_tokens``). The old
        ``RecursiveCharacterTextSplitter`` path returned bare strings, whose
        source positions could only be recovered by find-heuristics that
        mis-assign ranges on periodic content (the empty-grid table e000261
        rows); byte-identical overlap windows then collapsed to one chunk id.
        With exact ranges every emitted chunk can declare its
        ``source_occurrence``, identical texts at different positions keep
        deterministic distinct ids, and real occurrences are never confused
        with overlap windows.

        Window packing mirrors the old semantics: greedy seal-then-start
        packing of atomic pieces (recursive separator ladder, separator kept
        with the PRECEDING piece) under ``chunk_size`` tokens (soft budget
        clamped so prefix+window stays under the hard cap), with a
        trailing-piece overlap of at most ``self._overlap`` tokens and
        guaranteed forward progress. A piece that no separator can split
        (single unbreakable token/run) falls back to hard character slicing.
        """
        chunk_size = max(1, min(self._token_budget,
                                _HARD_CAP_TOKENS - prefix_tokens))
        overlap = min(self._overlap, max(0, chunk_size - 1))
        windows = self._pack_windows(body, chunk_size=chunk_size, overlap=overlap)
        return [(body[s:e], s, e) for s, e in windows]

    def _pack_windows(
        self, body: str, *, chunk_size: int, overlap: int
    ) -> list[tuple[int, int]]:
        pieces = self._atomic_pieces(body, chunk_size=chunk_size)
        windows: list[tuple[int, int]] = []
        cur: list[tuple[int, int]] = []
        cur_start = cur_end = 0

        def seal() -> None:
            nonlocal cur, cur_start, cur_end
            if cur:
                windows.append((cur_start, cur_end))
                cur = []

        for ps, pe in pieces:
            # An unbreakable atomic piece that alone exceeds the budget
            # (no deeper separator could split it): hard char-slice, no overlap.
            if self._count(body[ps:pe]) > chunk_size:
                seal()
                windows.extend(self._hard_slice(body, ps, pe, limit=chunk_size))
                cur_start = cur_end = pe
                continue
            if cur and self._count(body[cur_start:pe]) > chunk_size:
                # seal the current window, then rebuild the next window from
                # its trailing overlap pieces (before sealing they are the
                # pieces `cur` already holds)
                carry = self._trailing_overlap(cur, body, overlap)
                windows.append((cur_start, cur_end))
                if carry and self._count(body[carry[0][0]:pe]) > chunk_size:
                    carry = []  # overlap cannot fit beside the piece: drop it
                cur = carry
                cur_start = cur[0][0] if cur else ps
                cur_end = cur[-1][1] if cur else ps
            if not cur:
                cur_start = ps
            cur.append((ps, pe))
            cur_end = pe
        seal()
        return windows or ([(0, len(body))] if body else [])

    def _trailing_overlap(
        self, pieces: list[tuple[int, int]], body: str, overlap: int
    ) -> list[tuple[int, int]]:
        """Trailing pieces whose joined count fits within ``overlap`` tokens."""
        if overlap <= 0 or not pieces:
            return []
        taken: list[tuple[int, int]] = []
        total = 0
        for s, e in reversed(pieces):
            c = self._count(body[s:e])
            if total + c > overlap:
                break
            taken.insert(0, (s, e))
            total += c
        return taken

    def _atomic_pieces(
        self, text: str, separators: tuple[str, ...] | list[str] | None = None,
        *, chunk_size: int,
    ) -> list[tuple[int, int]]:
        """Atomic [start, end) pieces of ``text`` via the separator ladder.

        The first separator present in the text splits it (separator kept with
        the preceding piece); only pieces that ALONE exceed ``chunk_size``
        tokens recurse into the next separator level. The empty "" separator
        terminates the ladder: the piece is atomic and the caller may hard-slice.
        """
        if not text:
            return []
        ladder = list(_SEPARATORS if separators is None else separators)
        sep_i = None
        for i, s in enumerate(ladder):
            if s == "":
                break
            if s in text:
                sep_i = i
                break
        if sep_i is None:
            return [(0, len(text))]
        sep = ladder[sep_i]
        rest = ladder[sep_i + 1:]
        ranges: list[tuple[int, int]] = []
        start = 0
        n = len(text)
        while start < n:
            idx = text.find(sep, start)
            if idx == -1:
                ranges.append((start, n))
                break
            ranges.append((start, idx + len(sep)))
            start = idx + len(sep)
        if sep_i + 1 < len(ladder):
            out: list[tuple[int, int]] = []
            for s, e in ranges:
                if self._count(text[s:e]) > chunk_size:
                    # Recurse on the next separator level; sub-ranges are
                    # relative to text[s:e], so shift them back by ``s``.
                    out.extend((s + ss, s + se) for ss, se in self._atomic_pieces(
                        text[s:e], rest, chunk_size=chunk_size))
                else:
                    out.append((s, e))
            ranges = out
        return ranges

    def _hard_slice(
        self, text: str, start: int, end: int, *, limit: int
    ) -> list[tuple[int, int]]:
        """Contiguous char windows over [start, end), each <= ``limit`` tokens
        (binary-searched end). Last resort for an unbreakable piece."""
        out: list[tuple[int, int]] = []
        cur = start
        while cur < end:
            lo, hi = cur + 1, end
            best = end
            while lo <= hi:
                mid = (lo + hi) // 2
                if self._count(text[cur:mid]) <= limit:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            out.append((cur, best))
            cur = best
        return out

    def emit_structured(self, element: dict[str, Any], text: str) -> None:
        """Emit a table/formula/figure as one chunk, respecting the hard cap.

        Structured units are atomic by default, but a large table's projected
        text (caption + rows + notes) can exceed the 512-token bge-small-zh
        max_length -- an overlong atomic chunk would be silently truncated at
        embedding time, corrupting its vector. When ``prefix + text`` breaches
        ``_HARD_CAP_TOKENS``, split by line (``_split_body`` uses the recursive
        splitter with ``\\n`` separators) so each sub-chunk stays under the cap.
        Every sub-chunk binds the same ``element_id`` -- the trace chain stays
        intact, and ``compute_chunk_id`` derives distinct ids from the distinct
        sub-chunk texts. This mirrors ``emit_intra_clause_split``.
        """
        self.flush()
        section_path = element.get("section_path") or []
        prefix = self._cap_safe_prefix(self._prefix_for(section_path))
        full = prefix + text
        if self._count(full) <= _HARD_CAP_TOKENS:
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=full,
                section_path=section_path,
                elements=[element],
                element_types=[element["type"]],
                occurrence=[{"element_id": element["element_id"],
                             "start": 0, "end": len(text)}]
                if self._declare_occurrence else None,
            )
            return
        if self._declare_occurrence:
            # Overlong structured unit: split into offset-exact windows so
            # each sub-chunk <= hard cap; every window declares its range.
            for sub, ws, we in self._split_windows(
                    text, prefix_tokens=self._count(prefix)):
                self._emit_chunk(
                    element_ids=[element["element_id"]],
                    text=prefix + sub,
                    section_path=section_path,
                    elements=[element],
                    element_types=[element["type"]],
                    occurrence=[{"element_id": element["element_id"],
                                 "start": ws, "end": we}],
                )
            return
        # Legacy projection: verbatim pre-round-5 split, no occurrence.
        for sub in self._split_body(prefix, text):
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=prefix + sub,
                section_path=section_path,
                elements=[element],
                element_types=[element["type"]],
            )

    def emit_intra_clause_split(self, element: dict[str, Any], text: str) -> None:
        """Split a single overlong clause into sub-chunks (each carries prefix)."""
        self.flush()
        section_path = element.get("section_path") or []
        prefix = self._cap_safe_prefix(self._prefix_for(section_path))
        if not self._declare_occurrence:
            for sub in self._split_body(prefix, text):
                self._emit_chunk(
                    element_ids=[element["element_id"]],
                    text=prefix + sub,
                    section_path=section_path,
                    elements=[element],
                    element_types=["text"],
                )
            return
        for sub, ws, we in self._split_windows(text, prefix_tokens=self._count(prefix)):
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=prefix + sub,
                section_path=section_path,
                elements=[element],
                element_types=["text"],
                occurrence=[{"element_id": element["element_id"],
                             "start": ws, "end": we}],
            )

    def emit_formula(self, element: dict[str, Any]) -> None:
        """T09 (ledger docs): project a formula WITHOUT duplicating its "式中"
        explanation.

        With PRIMARY bindings the explanation is absorbed into the formula
        chunk (exactly once -- see :meth:`emit_formula_bound`). Without
        bindings (including every ambiguous four-requirement outcome) the
        formula chunk is the anchor + complete latex body ONLY; the stored
        ``context_text`` is never copied here, and any explanation stays in
        its own independent text chunk. A formula whose anchor alone breaches
        the hard cap emits a SHORT locator record (never an over-limit chunk,
        never a truncated latex) via :meth:`_emit_formula_locator`.
        """
        bindings = sorted(
            (b for b in element.get("explanation_bindings") or []
             if b.get("role") == "primary"),
            key=lambda b: b.get("order", 0))
        if not bindings:
            self.flush()
            section_path = element.get("section_path") or []
            prefix, anchor_body = self._formula_anchor_parts(element)
            if self._count(prefix + anchor_body) > _HARD_CAP_TOKENS:
                self._emit_formula_locator(element, section_path)
                return
            anchor_occ: list[dict[str, Any]] = []
            proj_f = _project_text(element)
            if proj_f and proj_f.startswith(anchor_body) and anchor_body:
                anchor_occ = [{"element_id": element["element_id"],
                               "start": 0, "end": len(anchor_body)}]
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=prefix + anchor_body,
                section_path=section_path,
                elements=[self._formula_anchor_view(element)],
                element_types=["formula"],
                occurrence=anchor_occ,
            )
            return
        explanation_elements = []
        for b in bindings:
            e = self._element_lookup.get(b.get("element_id"))
            if e is not None:
                explanation_elements.append(e)
        self.emit_formula_bound(element, explanation_elements)

    def emit_formula_bound(
        self,
        element: dict[str, Any],
        explanation_elements: list[dict[str, Any]],
    ) -> None:
        """T09: emit a formula chunk that absorbs its PRIMARY explanation.

        The formula anchor ([公式 N] + the COMPLETE latex body) is present in
        every shard, verbatim and never split. When the explanation fits, one
        chunk carries formula + explanation with the union of source spans.
        When it does not, the explanation is split into contiguous line blocks
        and EVERY shard re-carries the anchor + full body, with the shard's
        ``element_ids`` / ``source_spans`` / usage policy projected from the
        explanation sources the shard ACTUALLY contains (per-shard strictest
        source wins) -- never inherited wholesale, never fabricated.

        Absorption happens ONLY when the anchor leaves usable room under the
        soft token budget (mirrored exactly by the exclusion pre-pass --
        see :meth:`_formula_anchor_parts`). Otherwise the anchor is emitted as
        its own shard and the explanation keeps its independent chunks -- the
        explanation must never be lost. An anchor that alone exceeds the hard
        cap produces a SHORT locator record (the complete latex stays in the
        Canonical element); no chunk that reaches the embedder ever exceeds
        the hard cap.
        """
        self.flush()
        section_path = element.get("section_path") or []
        prefix, anchor_body = self._formula_anchor_parts(element)
        base = prefix + anchor_body
        # The anchor's exact range within the formula element's projection:
        # _project_formula emits header + latex + context, so the projection
        # starts with the anchor body verbatim (checked, never assumed).
        anchor_occ: list[dict[str, Any]] = []
        proj_f = _project_text(element)
        if proj_f and proj_f.startswith(anchor_body) and anchor_body:
            anchor_occ = [{"element_id": element["element_id"],
                           "start": 0, "end": len(anchor_body)}]
        if self._count(base) > _HARD_CAP_TOKENS:
            # Anchor overflow: short locator record only -- the over-limit
            # latex never enters the embedder stream. The locator text is a
            # synthesized pointer, NOT a slice of the projection: it declares
            # no occurrence ranges.
            self._emit_formula_locator(element, section_path)
            return
        if self._count(base) > self._token_budget:
            # Anchor too large to pack ANY explanation beside it: emit the
            # anchor shard alone (complete number + body, within the hard
            # cap). The pre-pass did not exclude the explanation for this
            # formula, so it keeps its own independent chunks -- never lost.
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=base,
                section_path=section_path,
                elements=[self._formula_anchor_view(element)],
                element_types=["formula"],
                occurrence=anchor_occ,
            )
            return
        # Greedily pack explanation LINES into blocks that fit under the
        # builder's soft token budget (hard cap stays the hard floor); each
        # line remembers its owning element AND its exact [start, end) range
        # within that element's projection for the per-shard occurrence.
        cap = max(1, self._token_budget)
        pieces: list[tuple[dict[str, Any], str, int, int]] = []
        for e in explanation_elements:
            proj = _project_text(e)
            if not proj:
                continue
            off = 0
            for line in proj.split("\n"):
                if line.strip():
                    pieces.append((e, line, off, off + len(line)))
                off += len(line) + 1
        blocks: list[list[tuple[dict[str, Any], str, int, int]]] = []
        current: list[tuple[dict[str, Any], str, int, int]] = []
        for e, line, ls, le in pieces:
            if current and self._count(
                    base + "\n" + "\n".join(l for _, l, _s, _e in current + [(e, line, ls, le)])) > cap:
                # the next line overflows the current block: seal it first
                blocks.append(current)
                current = []
            if current:
                current.append((e, line, ls, le))
                continue
            if self._count(base + "\n" + line) > cap:
                # a single explanation line alone would overflow: split the
                # line itself, each piece becomes its own shard (same owner);
                # the joiner accounts for the "\n" re-inserted between the
                # anchor and the piece so anchor + piece <= hard cap
                for sub, ss, se in self._split_windows(
                        line, prefix_tokens=self._count(base + "\n")):
                    if sub.strip():
                        blocks.append([(e, sub.strip(), ls + ss, ls + se)])
            else:
                current = [(e, line, ls, le)]
        if current:
            blocks.append(current)
        if not blocks:
            # Empty explanation (every bound element projects to nothing):
            # the formula text itself must still be emitted -- anchor shard.
            blocks = []
            self._emit_chunk(
                element_ids=[element["element_id"]],
                text=prefix + anchor_body,
                section_path=section_path,
                elements=[self._formula_anchor_view(element)],
                element_types=["formula"],
                occurrence=anchor_occ,
            )
        for block in blocks:
            block_text = "\n".join(l for _, l, _s, _e in block)
            owners: list[dict[str, Any]] = []
            seen_owners: set[str] = set()
            occurrence: list[dict[str, Any]] = list(anchor_occ)
            for e, _l, s, t in block:
                if e["element_id"] not in seen_owners:
                    seen_owners.add(e["element_id"])
                    owners.append(e)
                occurrence.append({"element_id": e["element_id"],
                                   "start": s, "end": t})
            self._emit_chunk(
                element_ids=[element["element_id"]] + [o["element_id"] for o in owners],
                text=prefix + anchor_body + "\n" + block_text,
                section_path=section_path,
                elements=[self._formula_anchor_view(element)] + owners,
                element_types=["formula"] + ["text"] * len(owners),
                occurrence=occurrence,
            )

    def _emit_text_chunk(
        self,
        units: list[_PendingUnit],
        prefix: str,
        body: str,
        *,
        occurrence: list[dict[str, Any]] | None = None,
    ) -> None:
        full = prefix + body
        elements = [self._element_lookup[u.element_id] for u in units]
        self._emit_chunk(
            element_ids=[u.element_id for u in units],
            text=full,
            section_path=units[0].section_path,
            elements=elements,
            element_types=["text"],
            occurrence=occurrence,
        )

    def _emit_chunk(
        self,
        *,
        element_ids: list[str],
        text: str,
        section_path: list[dict[str, Any]],
        elements: list[dict[str, Any]],
        element_types: list[str],
        extra_reasons: list[str] | None = None,
        occurrence: list[dict[str, Any]] | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            return
        # Round 5: the declared source occurrence ranges (element-relative
        # char ranges this chunk's text was cut from). Normalized here so the
        # id hash, the dedupe fingerprint, and the metadata all see the same
        # canonical shape.
        occurrence = [
            {"element_id": o["element_id"], "start": int(o["start"]),
             "end": int(o["end"])}
            for o in (occurrence or [])
        ]
        if not self._declare_occurrence:
            # legacy projection: identity and metadata stay exactly as
            # published -- no occurrence declaration, no id change.
            occurrence = []
        # 中央发块门禁 (central chunk-emission gate): this is the SINGLE choke
        # point every chunk passes through on its way to the embedder stream.
        # No chunk above the 512-token hard cap may ever be emitted -- the
        # bge-small-zh encoder would silently truncate it, corrupting the
        # vector. Every over-limit path upstream must degrade (drop the
        # prefix, shard, or emit a short locator record); if any path still
        # produces an over-limit chunk, this hard error surfaces it at build
        # time instead of letting a silently-truncated vector into Qdrant.
        n_tokens = self._count(text)
        if n_tokens > _HARD_CAP_TOKENS:
            raise ValueError(
                f"central chunk-emission gate: chunk for elements "
                f"{element_ids} measures {n_tokens} tokens > "
                f"{_HARD_CAP_TOKENS} hard cap -- over-limit chunks must never "
                f"reach the embedder (text head: {text[:60]!r})")
        source_spans = _collect_source_spans(elements)
        physical_pages = sorted({s.physical_page for s in source_spans})
        # usage policy is resolved BEFORE the dedupe so a same-id emission
        # with a different policy can be hard-failed instead of first-wins.
        policy, quality, reasons = chunk_usage_policy(elements, text)
        if extra_reasons:
            seen_r = set(reasons)
            for r in extra_reasons:
                if r not in seen_r:
                    seen_r.add(r)
                    reasons.append(r)
            # an explicit projection defect downgrades quality -- the chunk
            # can never look cleaner than its worst recorded issue.
            if quality == "passed":
                quality = "needs_review"
            # an anchor that alone breaches the hard cap is EXPLICITLY
            # demoted to locator-only: the over-limit shard is retained for
            # traceability but can never support an answer (T09 remediation).
            if REASON_ANCHOR_OVERFLOW in extra_reasons:
                policy = LOCATOR
        chunk_id = compute_chunk_id(
            canonical_content_id=self._canonical_content_id,
            element_ids=element_ids,
            projection_version=self._projection_version,
            text=text,
            occurrence=occurrence,
        )
        # T09 closure round 5: occurrence-aware deterministic dedupe. Round 4
        # dropped a repeated emission of the same (element_ids, text, spans) --
        # which wrongly collapsed REAL repeated source occurrences (u6b's 8
        # identical explanation lines; the empty-grid table 48429f37 e000261
        # rows). Now: byte-identical text at a DIFFERENT source position
        # hashes to a different id (occurrence in compute_chunk_id) and is
        # retained; only the re-emission of the SAME id with the SAME full
        # projection (spans + policy + quality + reasons + occurrence) is
        # dropped -- and the same id with ANY difference (especially policy/
        # reasons) is a HARD error, never first-wins.
        fingerprint = (
            tuple((s.physical_page, tuple(s.bbox)) for s in source_spans),
            policy,
            quality,
            tuple(reasons),
            tuple((o["element_id"], o["start"], o["end"]) for o in occurrence),
        )
        prior = self._emitted.get(chunk_id)
        if prior is not None:
            if prior != fingerprint:
                raise ValueError(
                    f"chunk-id uniqueness conflict: {chunk_id!r} was already "
                    "emitted with a DIFFERENT projection (source spans, "
                    "usage policy, quality, policy reasons, or occurrence) "
                    "-- content-addressed chunk ids must never collide; "
                    "refusing to build the chunk stream (no first-wins)")
            return  # identical projection already emitted: nothing new
        self._emitted[chunk_id] = fingerprint
        metadata = dict(self._doc_meta)
        metadata["element_types"] = element_types
        metadata["physical_pages"] = physical_pages
        metadata["section_path"] = [dict(e) for e in section_path]
        metadata["file_name"] = self._file_name
        metadata["source_sha256"] = self._source_sha
        # primary_evidence_id = chunk_id (1:1 chunk=evidence); set here because
        # it depends on chunk_id which was just computed. source_regions empty
        # in Phase 1 (region image rendering is deferred).
        metadata["primary_evidence_id"] = chunk_id
        metadata["source_regions"] = []
        # Round 5: declared source occurrence ranges (exact element-relative
        # char windows this chunk's text was cut from), formula-binding
        # projection only. Absent for legacy projections and for paths that
        # carry no positional slice of a projection (e.g. the synthesized
        # formula locator record) -- so legacy chunk metadata stays
        # byte-identical to what was published.
        if occurrence:
            metadata["source_occurrence"] = occurrence
        metadata["usage_policy"] = policy
        metadata["quality_status"] = quality
        metadata["policy_reasons"] = reasons
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
    projection_version: str | None = None,
    token_budget: int = 480,
    overlap: int = 64,
    tokenizer: Any = None,
) -> list[V2Chunk]:
    """Project a V2 Canonical Document into retrieval chunks.

    Parameters mirror plan §2.2. ``token_budget`` is the soft body target; the
    hard cap of 512 (bge-small-zh max) is always respected. ``tokenizer`` may be
    None (lazy bge tokenizer), an object with ``.count``/``.encode``, or a
    callable ``text -> int`` (for tests).

    ``projection_version``: when given, used verbatim. When omitted it is
    DERIVED from the document: a document carrying the T09
    ``formula_context_ledger`` marker is projected with the v2 formula-binding
    semantics (primary explanation absorbed into the formula chunks, per-shard
    source projection) under :data:`FORMULA_BINDING_PROJECTION_VERSION`;
    legacy documents without the marker keep the default projection (and its
    version string) byte-for-byte, so re-projecting a published corpus for
    verification never changes its chunk identities.
    """
    if not isinstance(canonical, dict):
        raise TypeError("canonical must be a dict")
    elements = canonical.get("elements") or []
    if not elements:
        return []
    valid_pages = {p["physical_page"] for p in (canonical.get("pages") or [])}
    count_fn = _resolve_tokenizer(tokenizer).count

    t09 = canonical.get("formula_context_ledger") is not None
    if projection_version is None:
        projection_version = (FORMULA_BINDING_PROJECTION_VERSION if t09
                              else DEFAULT_PROJECTION_VERSION)

    builder = _ChunkBuilder(
        canonical=canonical,
        projection_version=projection_version,
        token_budget=token_budget,
        overlap=overlap,
        count_fn=count_fn,
        valid_pages=valid_pages,
    )
    builder.bind_elements(elements)

    # T09 (ledger docs only): explanation elements that are PRIMARY-bound to a
    # formula leave ordinary text chunking -- they exist once, inside the
    # formula chunk. A formula whose anchor leaves no usable room under the
    # soft budget (overflow, or anchor too big to pack beside) cancels the
    # exclusion for its explanation: the explanation keeps its own independent
    # chunk -- never lost (T09 closure round 3).
    primary_owner: dict[str, dict[str, Any]] = {}
    # T09 remediation: text elements marked by the assembler as unbound
    # explanation candidates (a geometry walk that failed the four-requirement
    # binding). They must never be merged into the surrounding prose -- each
    # contiguous candidate run becomes its own chunk(s).
    unbound_candidates: set[str] = set()
    if t09:
        lookup = builder._element_lookup
        for element in elements:
            if (element.get("type") == "text" and any(
                    str(i).startswith("formula_explanation_unbound:")
                    for i in element.get("quality_issues") or [])):
                unbound_candidates.add(element["element_id"])
        for element in elements:
            if element.get("type") != "formula":
                continue
            primaries = [b for b in element.get("explanation_bindings") or []
                         if b.get("role") == "primary"]
            if not primaries:
                continue
            content = element.get("content") or {}
            number = content.get("formula_number")
            body = content.get("latex") or content.get("recognized_formula") or ""
            header = "[公式" + (f" {number}" if number else "") + "]"
            anchor = header + ("\n" + body.strip() if body else "")
            prefix = builder._prefix_for(element.get("section_path") or [])
            if (count_fn(prefix + anchor) > _HARD_CAP_TOKENS
                    and count_fn(anchor) <= _HARD_CAP_TOKENS):
                prefix = ""  # long section prefix: dropped, never the latex
            if count_fn(prefix + anchor) > builder._token_budget:
                # anchor overflow, or no usable room under the soft budget:
                # the explanation is NOT absorbed -- it keeps its independent
                # chunks (mirrors emit_formula_bound exactly -- never lost).
                continue
            for b in primaries:
                target = lookup.get(b.get("element_id"))
                if target is None:
                    continue
                owner = primary_owner.setdefault(b["element_id"], element)
                if owner is not element:
                    raise ValueError(
                        f"explanation element {b['element_id']!r} is "
                        f"primary-bound to multiple formulas (invalid "
                        f"explanation_bindings); refusing to project")

    prev_candidate = False
    for element in elements:
        etype = element.get("type")
        role = element.get("role")

        # T09 remediation: an unbound explanation candidate run never merges
        # with surrounding prose -- flush at BOTH run boundaries (before the
        # first candidate and before the first non-candidate that follows).
        is_candidate = element["element_id"] in unbound_candidates
        if etype == "text" and (is_candidate != prev_candidate):
            builder.flush()
        prev_candidate = is_candidate

        # Structured units are atomic: flush, then emit standalone.
        if etype in ("table", "formula", "figure"):
            if t09 and etype == "formula":
                builder.emit_formula(element)
                continue
            proj = _project_text(element)
            if proj:
                builder.emit_structured(element, proj)
            continue

        if etype == "text":
            if t09 and element["element_id"] in primary_owner:
                continue  # primary explanation: lives in the formula chunk
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
