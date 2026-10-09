"""Step-1 closure: reconcile golden_v1 anchors and emit the re-anchored golden_v2.

The v1 golden set was validated against ``corpus-069284e6d60e``.  The live
corpus is ``corpus-0b9a1fd84e3d`` (rebuilt by the T09 occurrence-aware chunk
identity round plus the QXT-489 OCR recovery), which renumbered the
document-scoped ``eNNNNNN`` element IDs and re-derived element quality, so the
v1 ``element_ids`` now alias unrelated content.  This script:

1. reconciles every v1 evidence row old-anchor -> new-anchor (page, elements,
   projected content, effective usage policy, quote verdict);
2. re-anchors the dataset onto the new corpus, keeping verbatim quotes as
   ``quote`` and moving humanized formula/table transcriptions to the new
   ``rendering`` field while rebuilding ``quote`` from a contiguous, locatable
   excerpt of the anchored Canonical element;
3. classifies per-evidence drift and emits the manual-pending checklist.

It is read-only with respect to the corpus, Qdrant and the published pointer;
it writes only under ``eval/``.  It never calls an LLM.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# Unconditional insert(0): this environment's PYTHONPATH also carries foreign
# project roots (e.g. D:\app_dev\ApisMind\backend) that expose their own ``src``
# package, and a membership-guarded insert is skipped when ROOT already appears
# later in sys.path -- which would bind ``import src`` to the foreign package.
for _candidate in (ROOT, Path.cwd()):
    if (_candidate / "src" / "application").is_dir():
        sys.path.insert(0, str(_candidate))

from src.application.evidence_policy import chunk_usage_policy  # noqa: E402
from src.application.v2_chunker import (  # noqa: E402
    _project_formula,
    _project_figure,
    _project_table,
    chunk_document,
)

OLD_TAG = "corpus-069284e6d60e"
NEW_TAG = "corpus-0b9a1fd84e3d"
SOURCE = ROOT / "eval" / "golden_v1.jsonl"
OUTPUT = ROOT / "eval" / "golden_v2.jsonl"
RECONCILIATION = ROOT / "eval" / "audits" / "step1_reconciliation.json"

# Quotes below must stay short enough to remain a single contiguous run of the
# anchored element's projected text (what chunks are built from).
_QUOTE_CAP_CHARS = 320
_SIM_THRESHOLD = 0.35
# Weak fallback floor: only used on the declared page when the primary window
# pass finds nothing (humanized table transcription vs re-OCR'd assembly).
_WEAK_SIM_THRESHOLD = 0.20
_ANCHOR_CAP = 8


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", value)


def _projected_text(element: dict[str, Any]) -> str:
    """Chunk-faithful projection of one Canonical element."""
    kind = element.get("type")
    content = element.get("content") or {}
    if kind == "text":
        return (content.get("text") or "").strip()
    if kind == "table":
        try:
            return _project_table(content)
        except KeyError:
            return ""
    if kind == "formula":
        return _project_formula(content)
    if kind == "figure":
        return _project_figure(content) or ""
    return ""


def _pages(element: dict[str, Any]) -> set[int]:
    return {
        span.get("physical_page")
        for span in element.get("source_spans") or []
        if span.get("physical_page")
    }


def _chunk_pages(chunk: Any) -> set[int]:
    return {
        span.physical_page
        for span in chunk.source_spans or []
        if span.physical_page
    }


def _chunk_page(chunk: Any, reference: int) -> int:
    """Provenance page of a chunk: the span page nearest the element-derived
    reference.  Whenever evidence element_ids become a chunk's element set,
    the page must come from that chunk too -- the element-level page vote can
    be pulled by a neighbour (e.g. a page-23 section heading) while the
    substantive chunk text lives on page 24."""
    pages = sorted(_chunk_pages(chunk))
    if not pages:
        return reference
    return min(pages, key=lambda p: (abs(p - reference), p))


# --------------------------------------------------------------------------- #
# Fact signals: numbers and CJK bigrams extracted from expected facts /
# transcriptions, used to centre the rebuilt quote window on the content the
# question actually needs (not merely on whatever element matched best).
# --------------------------------------------------------------------------- #

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _fact_signals(references: list[str]) -> tuple[set[str], set[str]]:
    """Extract (numbers, CJK bigrams) from the reference texts."""
    numbers: set[str] = set()
    bigrams: set[str] = set()
    for text in references:
        normalized = _normalized(text)
        numbers.update(_NUMBER_RE.findall(normalized))
        for i in range(len(normalized) - 1):
            a, b = normalized[i], normalized[i + 1]
            if "一" <= a <= "鿿" and "一" <= b <= "鿿":
                bigrams.add(a + b)
    numbers.discard("")
    return numbers, bigrams


def _signal_match(text_normalized: str, numbers: set[str], bigrams: set[str]) -> int:
    """How strongly a normalized text carries the reference signals."""
    if not text_normalized:
        return 0
    present = set(_NUMBER_RE.findall(text_normalized))
    matched_numbers = len(numbers & present)
    text_bigrams = {
        text_normalized[i] + text_normalized[i + 1]
        for i in range(len(text_normalized) - 1)
    }
    matched_bigrams = len(bigrams & text_bigrams)
    return 3 * matched_numbers + matched_bigrams


def _densest_center(text: str, facts: list[str], quote: str) -> int:
    """Character offset in ``text`` where the reference signals are densest.

    Expected facts take priority over the old transcription: the quote window
    must land on the content the question needs, and a mis-centred v1 quote
    (leaked adjacent sections, TOC lines) must not drag the window away from
    it.
    """
    normalized = _normalized(text)
    # Map normalized offsets back to raw offsets by walking both strings.
    raw_positions: list[int] = []
    raw_index = 0
    for ch in text:
        if _normalized(ch):
            raw_positions.append(raw_index)
        raw_index += 1

    def center_for(references: list[str]) -> tuple[int, int]:
        numbers, bigrams = _fact_signals(references)
        if not numbers and not bigrams:
            return 0, 0
        best_center, best_score = 0, 0
        window = max(40, min(len(normalized), 160))
        for start in range(0, max(1, len(normalized) - window + 1), 8):
            segment = normalized[start:start + window]
            score = _signal_match(segment, numbers, bigrams)
            if score > best_score:
                best_score = score
                best_center = start + window // 2
        return best_center, best_score

    for references in ([f for f in facts if f], [quote] if quote else []):
        center, score = center_for(references)
        if score > 0 and center < len(raw_positions):
            return raw_positions[center]
    return 0


def _fact_coverage(fact: str, quotes: list[str]) -> float:
    """Coverage of one expected fact by the evidence quotes (0..1).

    Numeric facts require their numbers to appear in a quote; textual facts
    are measured by shared CJK-bigram ratio.  The result is a conservative
    machine proxy for "the quote actually supports this fact" -- anything
    weak is flagged for human review, never silently accepted.
    """
    fact_normalized = _normalized(fact)
    if not fact_normalized:
        return 1.0
    numbers = set(_NUMBER_RE.findall(fact_normalized))
    quote_text = _normalized(" ".join(quotes))
    if numbers:
        present = set(_NUMBER_RE.findall(quote_text))
        return round(len(numbers & present) / len(numbers), 4)
    fact_bigrams = {
        fact_normalized[i] + fact_normalized[i + 1]
        for i in range(len(fact_normalized) - 1)
        if "一" <= fact_normalized[i] <= "鿿"
    }
    if not fact_bigrams:
        return 1.0
    quote_bigrams = {
        quote_text[i] + quote_text[i + 1]
        for i in range(len(quote_text) - 1)
    }
    return round(len(fact_bigrams & quote_bigrams) / len(fact_bigrams), 4)


def _similarity(reference: str, text: str) -> float:
    """Coverage of ``reference`` by ``text`` (both normalized, capped).

    The score is ``len(match) / len(reference)``: an element containing the
    whole reference scores 1.0; a fragment covering 40% of it scores 0.4.  The
    reverse containment (text inside reference) only counts when the text is a
    *substantial* part of the reference -- tiny fragments such as page numbers
    must never win the anchor.
    """
    ref, txt = _normalized(reference)[:600], _normalized(text)[:600]
    if not ref or not txt:
        return 0.0
    if ref in txt:
        return 1.0
    if txt in ref and len(txt) >= max(30, int(0.4 * len(ref))):
        return len(txt) / len(ref)
    longest = SequenceMatcher(None, ref, txt, autojunk=False).find_longest_match().size
    if longest < 12:
        return 0.0
    return longest / len(ref)


class Corpus:
    """Canonical documents of one corpus version, keyed by display file name."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        # Byte-level newline split: the documents JSONL carries NEL (U+0085)
        # and other characters inside page payloads that str.splitlines()
        # wrongly treats as line boundaries.
        raw = (
            ROOT / "data" / "canonical" / f"{tag}-documents.jsonl"
        ).read_bytes().decode("utf-8")
        rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
        self.documents: dict[str, dict[str, Any]] = {}
        self.page_texts: dict[str, dict[int, str]] = {}
        self.page_counts: dict[str, int] = {}
        for row in rows:
            path = ROOT / "data" / "canonical" / "v2" / tag / f"{row['sha256']}.canonical.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            self.documents[row["file_name"]] = document
            self.page_texts[row["file_name"]] = {
                page["physical_page"]: page.get("text") or "" for page in row["pages"]
            }
            self.page_counts[row["file_name"]] = len(row["pages"])

    def elements(self, file_name: str) -> list[dict[str, Any]]:
        return self.documents[file_name].get("elements") or []

    def by_id(self, file_name: str) -> dict[str, dict[str, Any]]:
        return {e["element_id"]: e for e in self.elements(file_name)}


def _classify_drift(old_el: dict[str, Any] | None, new_el_same_id: dict[str, Any] | None) -> str:
    if old_el is None:
        return "old_id_unresolved"
    if new_el_same_id is None:
        return "id_absent_in_new"
    old_pages, new_pages = _pages(old_el), _pages(new_el_same_id)
    old_text = _normalized(_projected_text(old_el))
    new_text = _normalized(_projected_text(new_el_same_id))
    same_content = bool(old_text) and bool(new_text) and (
        old_text in new_text or new_text in old_text
        or _similarity(old_text, new_text) >= 0.8
    )
    if same_content and old_pages & new_pages:
        return "id_stable"
    if same_content:
        return "id_stable_content_page_moved"
    if old_pages & new_pages:
        return "id_aliased_same_page"
    return "id_aliased_page_shifted"


def _reanchor(
    evidence: dict[str, Any],
    old: Corpus,
    new: Corpus,
    must_hit: bool,
    facts: list[str] | None = None,
    chunks: list[Any] | None = None,
) -> dict[str, Any]:
    """Find the new-corpus anchor for one evidence row.

    ``facts`` are the case's expected_facts: the rebuilt quote window is
    centred on them so the evidence actually carries the content the question
    needs (the old logic could land on an adjacent term/element).  ``chunks``
    are the offline-reconstructed retrieval chunks of the document, used so
    the quote is always a run of a REAL chunk's text.
    """
    chunks = chunks or []
    file_name = evidence["doc_file"]
    declared_page = evidence.get("page")
    old_by_id = old.by_id(file_name)
    old_elements = [old_by_id[i] for i in evidence.get("element_ids") or [] if i in old_by_id]
    old_pages = sorted({p for el in old_elements for p in _pages(el)})
    references = [
        _projected_text(el) for el in old_elements if _projected_text(el).strip()
    ]
    quote = evidence.get("quote") or ""
    if quote.strip():
        references.append(quote)

    window = set(old_pages) | ({declared_page} if declared_page else set())
    window = {p + d for p in window for d in (-3, -2, -1, 0, 1, 2, 3)}

    scored: list[tuple[float, float, int, dict[str, Any]]] = []
    weak = False
    for position, element in enumerate(new.elements(file_name)):
        if not (_pages(element) & window):
            continue
        text = _projected_text(element)
        if not text.strip():
            continue
        quote_coverage = _similarity(quote, text) if quote.strip() else 0.0
        old_coverage = max(
            (_similarity(ref, text) for ref in references[:-1]), default=0.0
        ) if len(references) > 1 else 0.0
        score = max(quote_coverage, old_coverage)
        if score >= _SIM_THRESHOLD:
            scored.append((score, quote_coverage, position, element))
    if not scored:
        # Weak fallback on the declared page: humanized table transcriptions can
        # drop below the window threshold against the re-OCR'd cell assembly
        # while the element is still unambiguously on the known page.
        if declared_page is not None:
            for position, element in enumerate(new.elements(file_name)):
                if _pages(element) != {declared_page}:
                    continue
                text = _projected_text(element)
                if not text.strip():
                    continue
                score = max((_similarity(ref, text) for ref in references), default=0.0)
                if score >= _WEAK_SIM_THRESHOLD:
                    scored.append((score, score, position, element))
                    weak = True
    if not scored:
        # Whole-document fallback for content that moved further than the window.
        for position, element in enumerate(new.elements(file_name)):
            text = _projected_text(element)
            if not text.strip():
                continue
            score = max((_similarity(ref, text) for ref in references), default=0.0)
            if score >= _SIM_THRESHOLD:
                scored.append((score, score, position, element))

    result: dict[str, Any] = {"resolved": bool(scored), "weak": weak}
    if not scored:
        return result

    anchors_by_id = new.by_id(file_name)
    # Rank by overall coverage with a quote-coverage tie-break, then keep
    # document order inside the selected set so a kept quote stays a
    # contiguous run of the joined anchor text.
    ranked = sorted(scored, key=lambda item: (-item[0], -item[1], item[2]))
    new_page = int(
        Counter(p for _, _, _, element in ranked[:3] for p in _pages(element))
        .most_common(1)[0][0]
    )
    selected = ranked[:_ANCHOR_CAP]
    selected.sort(key=lambda item: item[2])
    anchors = [item[3] for item in selected]
    # Trim anchors that live further than one page from the evidence page:
    # the ±3 search window legitimately pulls in cross-page continuations
    # (e.g. the second formula of a pair), but front-matter/TOC fragments two
    # or more pages away only pollute the anchor set.  Never trim to empty.
    near = [
        el for el in anchors
        if any(abs(p - new_page) <= 1 for p in _pages(el))
    ]
    if near:
        anchors = near

    if not must_hit:
        # Refusal locator: keep a short verbatim page excerpt, never a pseudo-quote.
        excerpt_parts: list[str] = []
        excerpt_ids: list[str] = []
        for element in new.elements(file_name):
            if _pages(element) != {new_page}:
                continue
            text = _projected_text(element)
            if not text.strip():
                continue
            excerpt_parts.append(text.strip())
            excerpt_ids.append(element["element_id"])
            if len(_normalized(" ".join(excerpt_parts))) >= 40:
                break
        result.update(
            anchors=excerpt_ids or [el["element_id"] for el in anchors],
            page=new_page,
            quote=" ".join(excerpt_parts) or _projected_text(anchors[0]),
            quote_kind="page_excerpt",
            rendering=None,
        )
        return result

    joined = _normalized(
        "\n".join(_projected_text(anchors_by_id[el["element_id"]]) for el in anchors)
    )
    quote_normalized = _normalized(quote)
    anchor_id_set = {el["element_id"] for el in anchors}
    containing = [
        c for c in chunks
        if quote_normalized and quote_normalized in _normalized(c.text)
        and set(c.element_ids) & anchor_id_set
    ]
    if quote_normalized and quote_normalized in joined and containing:
        # Keep the verbatim quote.  The evidence element_ids are ALWAYS the
        # containing chunk's full element set -- the quote is chunk-level
        # text, so its provenance (ids, page, usage_policy) is the chunk, and
        # a subset of the chunk's elements could project a looser policy than
        # the chunk retrieval can actually support (G026[0]).
        best_containing = max(
            containing, key=lambda c: len(set(c.element_ids) & anchor_id_set)
        )
        spans_multiple = not any(
            quote_normalized
            in _normalized(_projected_text(anchors_by_id[element_id]))
            for element_id in anchor_id_set
            if element_id in anchors_by_id
        )
        result.update(
            anchors=list(best_containing.element_ids),
            page=_chunk_page(best_containing, new_page),
            quote=quote,
            quote_kind="verbatim_kept",
            rendering=None,
            chunk_ids=list(best_containing.element_ids),
            chunk_text=best_containing.text,
            spans_multiple=spans_multiple,
        )
        return result

    # Chunk-aware rebuild.  The locatable quote is a window of an ACTUAL
    # chunk's text (what retrieval can ever return), centred on the densest
    # match with the case's expected facts plus the old transcription; the
    # evidence element_ids become the chosen chunk's element set -- the true
    # provenance of that text.  A quote that lives in no single chunk can
    # never be matched by the (strict, containment-based) harness matcher.
    candidates = [c for c in chunks if set(c.element_ids) & anchor_id_set]
    if not candidates:
        # No chunk lists the anchored elements (e.g. a heading element that
        # only ever appears as chunk scaffolding text).  Fall back to chunks
        # whose text actually contains the joined anchor projection -- the
        # quote stays locatable and its provenance becomes that chunk.
        best_text = "\n".join(
            _projected_text(anchors_by_id[el["element_id"]]) for el in anchors
        ).strip()
        if len(best_text) > _QUOTE_CAP_CHARS:
            center = _densest_center(best_text, facts or [], quote)
            best_text = best_text[max(0, center - 80): center + _QUOTE_CAP_CHARS - 80]
        best_normalized = _normalized(best_text)
        containers = [
            c for c in chunks
            if best_normalized and best_normalized in _normalized(c.text)
        ]
        if containers:
            fact_numbers, fact_bigrams = _fact_signals(facts or [])
            quote_numbers, quote_bigrams = _fact_signals([quote])
            best_chunk = max(
                containers,
                key=lambda c: _signal_match(
                    _normalized(c.text), fact_numbers, fact_bigrams
                ) or _signal_match(
                    _normalized(c.text), quote_numbers, quote_bigrams
                ),
            )
            result.update(
                anchors=list(best_chunk.element_ids),
                page=_chunk_page(best_chunk, new_page),
                quote=best_text,
                quote_kind="rebuilt_element_excerpt",
                rendering=quote,
                chunk_ids=list(best_chunk.element_ids),
                chunk_text=best_chunk.text,
            )
            return result
        result.update(
            anchors=[el["element_id"] for el in anchors],
            page=new_page,
            quote=best_text or quote,
            quote_kind="rebuilt_element_excerpt" if best_text else "unresolved_excerpt",
            rendering=quote if best_text else None,
            no_chunk_candidate=True,
        )
        return result

    def chunk_score(c: Any) -> tuple[int, int, int]:
        text = _normalized(c.text)
        fact_numbers, fact_bigrams = _fact_signals(facts or [])
        quote_numbers, quote_bigrams = _fact_signals([quote])
        # Facts dominate: the evidence must carry the content the question
        # needs.  The old transcription is only a tie-break -- v1 quotes can
        # themselves be mis-centred (leaked adjacent sections, TOC lines),
        # so they must never outvote the expected facts.
        fact_match = _signal_match(text, fact_numbers, fact_bigrams)
        quote_match = _signal_match(text, quote_numbers, quote_bigrams)
        overlap = len(set(c.element_ids) & anchor_id_set)
        return (fact_match, quote_match, overlap)

    if facts and candidates:
        # Fact-coverage gate: if the best anchor-intersecting chunk does not
        # actually carry the expected facts (numbers missing, key terms only
        # partially present), fall back to a fact-driven search over the whole
        # document's chunks so the evidence lands on the substantive section
        # (table, formula, body) instead of a mis-anchored neighbour.
        def fact_coverage_of(c: Any) -> float:
            return min(
                (_fact_coverage(f, [c.text]) for f in facts), default=1.0
            )

        provisional = max(candidates, key=chunk_score)
        if fact_coverage_of(provisional) < 0.50:
            # Any chunk that actually carries the facts is eligible -- even
            # when its aggregate signal score is lower than the mis-anchored
            # neighbour's (a fact-light intro can out-score a fact-rich table
            # on shared boilerplate bigrams).  Coverage first, score second.
            document_wide = [
                c for c in chunks if fact_coverage_of(c) >= 0.50
            ]
            if document_wide:
                candidates = document_wide

    best_chunk = max(candidates, key=chunk_score)
    best_text = best_chunk.text.strip()
    if len(best_text) > _QUOTE_CAP_CHARS:
        center = _densest_center(best_text, facts or [], quote)
        best_text = best_text[max(0, center - 80): center + _QUOTE_CAP_CHARS - 80]
    if len(_normalized(best_text)) < 20:
        best_text = best_chunk.text.strip()[:_QUOTE_CAP_CHARS]
    result.update(
        anchors=list(best_chunk.element_ids),
        page=_chunk_page(best_chunk, new_page),
        quote=best_text,
        quote_kind="rebuilt_element_excerpt",
        rendering=quote,
        chunk_ids=list(best_chunk.element_ids),
        chunk_text=best_chunk.text,
    )
    return result


def _quote_verdict(quote: str, elements: list[dict[str, Any]], page_text: str) -> str:
    quote_normalized = _normalized(quote)
    if not quote_normalized:
        return "empty"
    element_text = _normalized("\n".join(_projected_text(el) for el in elements))
    if quote_normalized in element_text:
        return "element_verbatim"
    if quote_normalized in _normalized(page_text):
        return "page_verbatim"
    return "non_verbatim_transcription"


def _table_row(document: dict[str, Any], page: int, first_cell: str) -> tuple[str, str] | None:
    """Locate a table row verbatim (used for the documented G032 evidence add)."""
    for element in document.get("elements") or []:
        if element.get("type") != "table" or _pages(element) != {page}:
            continue
        content = element.get("content") or {}
        grid_row: dict[int, str] = {}
        for cell in content.get("cells") or []:
            if cell["row"] not in grid_row and (cell.get("text") or "") == first_cell:
                target_row = cell["row"]
                grid_row = {
                    c["column"]: c.get("text") or ""
                    for c in content.get("cells") or []
                    if c["row"] == target_row
                }
        if grid_row:
            row = " | ".join(grid_row[c] for c in sorted(grid_row))
            return element["element_id"], row
    return None


def main() -> None:
    old, new = Corpus(OLD_TAG), Corpus(NEW_TAG)
    cases = [
        json.loads(line)
        for line in SOURCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Offline-reconstructed retrieval chunks of every document.  chunk_document
    # is deterministic (same projection/version as the published build), so a
    # quote that appears in one of these texts is a quote retrieval can ever
    # return -- this is what makes the rebuilt quotes chunk-faithful.
    chunks_by_file: dict[str, list[Any]] = {
        file_name: chunk_document(document)
        for file_name, document in new.documents.items()
    }

    # Documented addition: G032's answer fact (1h 8.0~19.9 -> 短时暴雨) lives in
    # the page-6 table, while the v1 evidence only anchored the page-5 lead-in.
    additions: dict[str, list[dict[str, Any]]] = {"G032": []}
    g032_row = _table_row(
        new.documents["短时气象服务降雨量等级TCMSA-2019.pdf"], 6, "短时暴雨"
    )
    if g032_row:
        element_id, row_text = g032_row
        additions["G032"].append(
            {
                "doc_file": "短时气象服务降雨量等级TCMSA-2019.pdf",
                "standard_number": "T/CMSA 0013-2019",
                "page": 6,
                "quote": row_text,
                "must_hit": True,
                "element_ids": [element_id],
                "_native": True,
                "_note": "added: answer fact lives in the page-6 table 1 row",
            }
        )

    reconciliation: list[dict[str, Any]] = []
    output_rows: list[dict[str, Any]] = []

    for case in cases:
        case_id = case["id"]
        evidences = list(case.get("evidence") or [])
        for extra in additions.get(case_id, []):
            evidences.append(extra)
        new_evidences: list[dict[str, Any]] = []
        case_records: list[dict[str, Any]] = []
        refusal = "拒答" in (case.get("labels") or {}).get("repr", [])

        for index, evidence in enumerate(evidences):
            must_hit = evidence.get("must_hit", True)
            if evidence.get("_native"):
                # Already anchored to the new corpus by construction.  The
                # policy still describes the CARRYING chunk (full element set
                # + chunk text), consistent with every other quote kind.
                native_by_id = new.by_id(evidence["doc_file"])
                native_elements = [
                    native_by_id[i] for i in evidence.get("element_ids") or []
                    if i in native_by_id
                ]
                merged = "\n".join(_projected_text(el) for el in native_elements)
                native_page = evidence["page"]
                native_ids = list(evidence["element_ids"])
                row_normalized = _normalized(evidence["quote"])
                native_chunks = chunks_by_file.get(evidence["doc_file"], [])
                carrying = [
                    c for c in native_chunks
                    if row_normalized and row_normalized in _normalized(c.text)
                    and set(native_ids) <= set(c.element_ids)
                    and native_page in _chunk_pages(c)
                ]
                if carrying:
                    carrier = max(
                        carrying, key=lambda c: len(set(c.element_ids) & set(native_ids))
                    )
                    native_elements = [
                        native_by_id[i] for i in carrier.element_ids
                        if i in native_by_id
                    ]
                    merged = carrier.text
                    native_ids = list(carrier.element_ids)
                    native_page = _chunk_page(carrier, native_page)
                policy, quality, reasons = chunk_usage_policy(native_elements, merged)
                new_evidences.append(
                    {
                        "doc_file": evidence["doc_file"],
                        "standard_number": evidence["standard_number"],
                        "page": native_page,
                        "quote": evidence["quote"],
                        "must_hit": must_hit,
                        "element_ids": native_ids,
                        "usage_policy": policy,
                        "quote_kind": "native_table_row",
                        "notes": evidence["_note"],
                    }
                )
                reconciliation.append(
                    {
                        "case_id": case_id,
                        "evidence_index": index,
                        "doc_file": evidence["doc_file"],
                        "must_hit": must_hit,
                        "old": None,
                        "drift_classification": {"native_addition": 1},
                        "new": {
                            "resolved": True,
                            "page": native_page,
                            "element_ids": native_ids,
                            "anchor_text_heads": [
                                _projected_text(el)[:60] for el in native_elements
                            ],
                            "usage_policy": policy,
                            "quality_status": quality,
                            "policy_reasons": reasons,
                            "quote_kind": "native_table_row",
                            "quote": evidence["quote"],
                            "rendering": None,
                        },
                        "pending": [],
                    }
                )
                continue
            old_by_id = old.by_id(evidence["doc_file"])
            new_by_id = new.by_id(evidence["doc_file"])
            old_elements = [
                old_by_id[i] for i in evidence.get("element_ids") or [] if i in old_by_id
            ]
            old_anchor = [
                {
                    "id": i,
                    "pages": sorted(_pages(old_by_id[i])) if i in old_by_id else None,
                    "type": (old_by_id.get(i) or {}).get("type"),
                    "text_head": _projected_text(old_by_id[i])[:60] if i in old_by_id else None,
                }
                for i in evidence.get("element_ids") or []
            ]
            same_id_new = [
                (new_by_id.get(i), old_by_id.get(i)) for i in evidence.get("element_ids") or []
            ]
            drift = Counter(
                _classify_drift(o, n) for n, o in same_id_new if o is not None
            )

            anchor = _reanchor(
                evidence, old, new, must_hit,
                facts=[f for f in case.get("expected_facts") or [] if f],
                chunks=chunks_by_file.get(evidence["doc_file"], []),
            )
            quote_verdict = _quote_verdict(
                evidence.get("quote") or "",
                old_elements,
                old.page_texts.get(evidence["doc_file"], {}).get(evidence.get("page"), ""),
            )

            pending: list[str] = []
            policy = quality = None
            reasons: list[str] = []
            if anchor.get("resolved"):
                anchor_elements = [
                    new_by_id[i] for i in anchor["anchors"] if i in new_by_id
                ]
                merged = "\n".join(_projected_text(el) for el in anchor_elements)
                # usage_policy must describe the chunk that actually carries
                # the quote (full element set + chunk text for the readability
                # floor) -- a subset of the chunk's elements can project a
                # looser policy than retrieval can support (G026[0]).
                if anchor.get("chunk_ids"):
                    carrying_elements = [
                        new_by_id[i] for i in anchor["chunk_ids"] if i in new_by_id
                    ]
                    if carrying_elements:
                        anchor_elements = carrying_elements
                        merged = anchor.get("chunk_text") or merged
                policy, quality, reasons = chunk_usage_policy(anchor_elements, merged)
                if anchor["quote_kind"] == "rebuilt_element_excerpt":
                    pending.append("pdf_rendering_faithfulness")
                if anchor.get("weak"):
                    pending.append("weak_anchor_needs_review")
                elif anchor["quote_kind"] == "unresolved_excerpt":
                    pending.append("quote_unresolved")
                if anchor.get("no_chunk_candidate"):
                    pending.append("quote_not_in_single_chunk")
                if anchor.get("spans_multiple"):
                    pending.append("quote_spans_multiple_elements")
                if anchor["page"] != evidence.get("page"):
                    pending.append("page_reanchored")
            else:
                pending.append("anchor_unresolved")

            record = {
                "case_id": case_id,
                "evidence_index": index,
                "doc_file": evidence["doc_file"],
                "must_hit": must_hit,
                "old": {
                    "page": evidence.get("page"),
                    "anchor": old_anchor,
                    "quote_verdict_in_old_corpus": quote_verdict,
                },
                "drift_classification": dict(drift),
                "new": {
                    "resolved": anchor.get("resolved", False),
                    "page": anchor.get("page"),
                    "element_ids": anchor.get("anchors"),
                    "anchor_text_heads": [
                        _projected_text(new_by_id[i])[:60]
                        for i in anchor.get("anchors") or []
                        if i in new_by_id
                    ],
                    "usage_policy": policy,
                    "quality_status": quality,
                    "policy_reasons": reasons,
                    "quote_kind": anchor.get("quote_kind"),
                    "quote": anchor.get("quote"),
                    "rendering": anchor.get("rendering"),
                },
                "pending": pending,
            }
            case_records.append(record)

            new_evidence = {
                "doc_file": evidence["doc_file"],
                "quote": anchor.get("quote") or evidence.get("quote"),
                "must_hit": must_hit,
                "page": anchor.get("page") or evidence.get("page"),
                "element_ids": anchor.get("anchors") or evidence.get("element_ids") or [],
            }
            if evidence.get("standard_number"):
                new_evidence["standard_number"] = evidence["standard_number"]
            if anchor.get("rendering"):
                new_evidence["rendering"] = anchor["rendering"]
            if policy:
                new_evidence["usage_policy"] = policy
            if anchor.get("quote_kind"):
                new_evidence["quote_kind"] = anchor["quote_kind"]
            if pending:
                new_evidence["pending"] = pending
            if evidence.get("_note"):
                new_evidence["notes"] = evidence["_note"]
            new_evidences.append(new_evidence)

        must_hit_policies = [
            e.get("usage_policy") for e in new_evidences if e.get("must_hit", True)
        ]
        if refusal:
            answerability = "refuse"
        elif must_hit_policies and all(p == "answer_and_citation" for p in must_hit_policies):
            answerability = "answer"
        else:
            answerability = "locate"

        # Fact coverage: every expected fact of a non-refusal case must be
        # carried (numbers verbatim / text by bigram ratio) by the must_hit
        # quotes.  Weak facts are flagged for human review on every must_hit
        # evidence of the case -- never silently accepted as covered.
        fact_coverage: dict[str, float] = {}
        if not refusal:
            must_hit_quotes = [
                e.get("quote") or "" for e in new_evidences if e.get("must_hit", True)
            ]
            for fact in case.get("expected_facts") or []:
                coverage = _fact_coverage(fact, must_hit_quotes)
                fact_coverage[fact[:80]] = coverage
                if coverage < 0.50:
                    for e in new_evidences:
                        if e.get("must_hit", True):
                            e.setdefault("pending", [])
                            if "fact_coverage_needs_review" not in e["pending"]:
                                e["pending"].append("fact_coverage_needs_review")
                    for rec in case_records:
                        if rec["must_hit"] and "fact_coverage_needs_review" not in rec["pending"]:
                            rec["pending"].append("fact_coverage_needs_review")
        if fact_coverage:
            for rec in case_records:
                rec.setdefault("fact_coverage", {}).update(fact_coverage)

        output_rows.append(
            {
                "id": case_id,
                "question": case["question"],
                "evidence": new_evidences,
                "expected_facts": case["expected_facts"],
                "labels": case["labels"],
                "doc_coverage": case["doc_coverage"],
                "expected_answerability": answerability,
                "anchor_corpus": NEW_TAG,
                "status": "reviewed",
                **({"notes": case["notes"]} if case.get("notes") else {}),
            }
        )
        reconciliation.extend(case_records)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
        encoding="utf-8",
    )

    page_deltas = {
        name: {"old_pages": old.page_counts[name], "new_pages": new.page_counts[name]}
        for name in sorted(new.page_counts)
    }
    payload = {
        "old_corpus": OLD_TAG,
        "new_corpus": NEW_TAG,
        "source_dataset": "eval/golden_v1.jsonl",
        "source_dataset_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "output_dataset": "eval/golden_v2.jsonl",
        "output_dataset_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "document_page_counts": page_deltas,
        "evidence_count": len(reconciliation),
        "records": reconciliation,
    }
    RECONCILIATION.parent.mkdir(parents=True, exist_ok=True)
    RECONCILIATION.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    drift_totals: Counter[str] = Counter()
    for record in reconciliation:
        drift_totals.update(record["drift_classification"])
    quote_verdicts: Counter[str] = Counter(
        record["old"]["quote_verdict_in_old_corpus"]
        for record in reconciliation
        if record["old"] is not None
    )
    pending_totals: Counter[str] = Counter()
    for record in reconciliation:
        pending_totals.update(record["pending"])
    answerability: Counter[str] = Counter(row["expected_answerability"] for row in output_rows)
    print(json.dumps({
        "evidence_count": len(reconciliation),
        "drift": dict(drift_totals),
        "old_quote_verdicts": dict(quote_verdicts),
        "pending": dict(pending_totals),
        "answerability": dict(answerability),
        "output_sha256": payload["output_dataset_sha256"],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
