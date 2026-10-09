"""Corpus-wide occurrence-coverage reconciliation (T09 closure round 5).

Read-only over a candidate's artifacts; the ONLY write is the evidence JSON
in ``data/observability/``.

Verifies, for EVERY canonical document of the candidate, that the chunk
stream's declared ``source_occurrence`` ranges (element-relative character
windows -- round 5) cover the element projections by SOURCE POSITION, never
merely as a text set:

* every non-empty line occurrence of a text/table/figure element's projection
  must be fully covered by at least one declared range of a chunk listing
  that element (a multiplicity of 0 is a defect);
* the coverage multiplicity per line distinguishes real duplicate rows (each
  covered at least once at its OWN position) from splitter overlap windows
  (re-carrying, multiplicity > 1 -- allowed, reported);
* byte-identical chunk texts must occupy DIFFERENT declared range sets
  (identical text at a different source position is a different occurrence);
* formula elements: the required coverage is the anchor (header + latex).
  The context tail stays in the Canonical element (locator-only D3) and is
  reported separately, not failed. A formula whose only chunks are anchor-
  overflow locator records (``anchor_exceeds_token_cap``) carries no
  positional slice and is excluded from the requirement, counted separately;
* per-document chunk counts are cross-checked against the stored
  chunk_manifest, and chunk ids must be unique.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from src.application.v2_chunker import chunk_document, _project_text  # noqa: E402

REASON_ANCHOR_OVERFLOW = "anchor_exceeds_token_cap"


def _line_spans(text: str) -> list[tuple[int, int]]:
    """[start, end) ranges of every non-empty line in ``text``."""
    out: list[tuple[int, int]] = []
    off = 0
    for line in text.split("\n"):
        if line.strip():
            out.append((off, off + len(line)))
        off += len(line) + 1
    return out


def _anchor_body(element: dict) -> str:
    content = element.get("content") or {}
    number = content.get("formula_number")
    body = content.get("latex") or content.get("recognized_formula") or ""
    header = "[公式" + (f" {number}" if number else "") + "]"
    return header + ("\n" + body.strip() if body else "")


def audit_document(doc: dict, chunks: list) -> dict:
    """Positional occurrence reconciliation for one document."""
    # declared ranges per element + locator-only elements + text groups
    declared: dict[str, list[tuple[int, int]]] = {}
    locator_only_elements: set[str] = set()
    text_groups: dict[str, set] = {}
    for c in chunks:
        md = c.metadata or {}
        text_groups.setdefault(c.text, set()).add(
            tuple((o["element_id"], o["start"], o["end"])
                  for o in (md.get("source_occurrence") or [])))
        if REASON_ANCHOR_OVERFLOW in (md.get("policy_reasons") or []):
            locator_only_elements.update(c.element_ids)
        for o in (md.get("source_occurrence") or []):
            declared.setdefault(o["element_id"], []).append(
                (o["start"], o["end"]))
        declared.setdefault("_touched", [])  # placeholder, removed below
    declared.pop("_touched", None)

    # a text seen in >1 chunk must have >1 distinct occurrence sets (each
    # carried position is a different occurrence); count violating texts
    identical_text_collisions = 0
    seen_counts: dict[str, int] = {}
    for c in chunks:
        seen_counts[c.text] = seen_counts.get(c.text, 0) + 1
    for text, occs in text_groups.items():
        if seen_counts.get(text, 0) > 1 and len(occs) != seen_counts[text]:
            identical_text_collisions += 1

    per_element = []
    uncovered_total = 0
    formula_context_lines = 0
    locator_only_count = 0
    headings_via_prefix = 0
    for element in (doc.get("elements") or []):
        eid = element["element_id"]
        proj = _project_text(element)
        if proj is None:
            continue
        if element["type"] == "text" and element.get("role") == "heading":
            # heading text is carried by the section_path prefix of the
            # subsequent chunks (plan §2.5), never as a source_occurrence
            # slice -- excluded from the coverage requirement, counted.
            headings_via_prefix += 1
            continue
        ranges = declared.get(eid) or []
        lines = _line_spans(proj)
        is_formula = element["type"] == "formula"
        is_locator = eid in locator_only_elements
        if is_locator:
            locator_only_count += 1
            required: list[tuple[int, int]] = []
            excluded = lines
        elif is_formula:
            # only the anchor portion is REQUIRED in the chunk stream; the
            # context tail stays in the Canonical element (locator-only D3)
            anchor = _anchor_body(element)
            if anchor and proj.startswith(anchor):
                anchor_len = len(anchor)
            else:
                anchor_len = len(proj)  # nothing declarable: require all
            required = [(s, e) for s, e in lines if e <= anchor_len]
            excluded = [(s, e) for s, e in lines if e > anchor_len]
            formula_context_lines += len(excluded)
        else:
            required = lines
            excluded = []
        uncovered = [
            (s, e) for s, e in required
            if not any(a <= s and e <= b for (a, b) in ranges)
        ]
        covered_once = covered_multi = 0
        for s, e in required:
            hits = sum(1 for (a, b) in ranges if a <= s and e <= b)
            if hits == 1:
                covered_once += 1
            elif hits > 1:
                covered_multi += 1
        uncovered_total += len(uncovered)
        per_element.append({
            "element_id": eid,
            "type": element["type"],
            "locator_only": is_locator,
            "declared_ranges": len(ranges),
            "line_occurrences": len(lines),
            "required_occurrences": len(required),
            "covered_once": covered_once,
            "covered_by_overlap": covered_multi,
            "uncovered": len(uncovered),
            "excluded_context_lines": len(excluded),
        })
    return {
        "chunk_count": len(chunks),
        "chunk_ids_unique": (
            len({c.chunk_id for c in chunks}) == len(chunks)),
        "element_count": len(per_element),
        "headings_carried_via_prefix": headings_via_prefix,
        "locator_only_elements": locator_only_count,
        "formula_context_lines_excluded": formula_context_lines,
        "identical_text_collision_texts": identical_text_collisions,
        "uncovered_required_line_occurrences": uncovered_total,
        "per_element": per_element,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("corpus_version")
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else _REPO_ROOT
    cv = args.corpus_version
    cand = root / "data" / "canonical" / "v2" / cv
    manifest_path = cand / "chunk_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"[audit] chunk manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    canonical_paths = sorted(cand.glob("*.canonical.json"))
    if not canonical_paths:
        raise SystemExit(f"[audit] no canonical documents under {cand}")

    per_doc = []
    total_chunks = 0
    total_uncovered = 0
    total_collisions = 0
    for path in canonical_paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("formula_context_ledger") is None:
            raise SystemExit(
                f"[audit] {path.name} carries no formula_context_ledger "
                "marker -- the occurrence audit applies to the T09 "
                "formula-binding projection only")
        chunks = chunk_document(doc)
        res = audit_document(doc, chunks)
        res["document_file"] = path.name
        res["file_name"] = (doc.get("source") or {}).get("file_name", "")
        res["canonical_id"] = doc.get("canonical_id", "")
        per_doc_chunk = (manifest.get("per_document") or {}).get(
            doc.get("canonical_id", ""))
        res["manifest_chunk_count"] = (
            per_doc_chunk.get("total_chunks")
            if isinstance(per_doc_chunk, dict) else per_doc_chunk)
        res["count_matches_manifest"] = (
            res["manifest_chunk_count"] in (None, res["chunk_count"]))
        total_chunks += res["chunk_count"]
        total_uncovered += res["uncovered_required_line_occurrences"]
        total_collisions += res["identical_text_collision_texts"]
        per_doc.append(res)

    failed = (
        total_uncovered > 0
        or total_collisions > 0
        or any(not d["chunk_ids_unique"] or not d["count_matches_manifest"]
               for d in per_doc)
        or total_chunks != manifest.get("total_chunks")
    )
    evidence = {
        "kind": "t09-closure-round5-occurrence-reconciliation",
        "corpus_version": cv,
        "result": "FAIL" if failed else "PASS",
        "total_chunks": total_chunks,
        "manifest_total_chunks": manifest.get("total_chunks"),
        "document_count": len(per_doc),
        "uncovered_required_line_occurrences": total_uncovered,
        "identical_text_collision_texts": total_collisions,
        "per_document": per_doc,
    }
    out = root / "data" / "observability" / (
        f"t09_round5_occurrence_reconciliation_{cv}.json")
    if out.exists():
        raise SystemExit(f"[audit] evidence file already exists: {out}")
    out.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"[audit] result: {evidence['result']}")
    print(f"[audit] evidence: {out}")
    print(f"[audit] chunks={total_chunks} docs={len(per_doc)} "
          f"uncovered={total_uncovered} collisions={total_collisions}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
