"""Re-anchor a golden dataset's evidence to the published corpus.

Every publish changes chunk text, IDs and sometimes pages, and the runners
refuse a dataset whose ``anchor_corpus`` is not the live corpus.  For each
evidence row this finds the chunk of the same document (same page first,
then the neighbouring pages) that best covers the clean source rendering
(or the old quote), and rewrites ``quote`` as the covering lines copied
verbatim from that chunk, with its page, element IDs and usage policy.

Rows whose best chunk covers too little of the source keep their old anchor
and get the ``reanchor_unresolved`` pending marker for a human to decide;
nothing is forced.  Questions, expected facts, answerability and status are
never touched.

    .venv\\Scripts\\python.exe scripts\\reanchor_golden.py \\
        --source eval/golden_v4.jsonl --output eval/golden_v5.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [path for path in sys.path if path != str(ROOT)]
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient  # noqa: E402

from src.evaluation.retrieval_harness import _quote_matches  # noqa: E402

UNRESOLVED = "reanchor_unresolved"
# The shared runs must cover most of the shorter side (the evidence inside a
# long chunk, or a short chunk inside a long rendering) ...
MIN_OVERLAP = 0.6
# ... and be long enough not to be a heading or a unit.
MIN_SHARED = 20
_LINE_SHARE = 0.6


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z一-鿿]+", "", value)


def _overlap(target: str, text: str) -> tuple[float, int]:
    """Overlap coefficient over shared runs, and the shared length.

    Evidence is usually part of a longer chunk, and a clean rendering can run
    past one chunk into the next, so the shared runs are measured against
    the shorter side.  Runs shorter than six characters ("的", "mm", clause
    digits) match anything on the page and are ignored.
    """
    if not target or not text:
        return 0.0, 0
    blocks = SequenceMatcher(None, target, text, autojunk=False).get_matching_blocks()
    shared = sum(block.size for block in blocks if block.size >= 6)
    return shared / min(len(target), len(text)), shared


def _covering_lines(target: str, text: str) -> str:
    """The contiguous chunk lines that carry the target, verbatim."""
    lines = text.split("\n")
    keep = []
    for index, line in enumerate(lines):
        norm = _normalized(line)
        # The chunk's first line is its heading path, not evidence content.
        if not norm or (index == 0 and " > " in line):
            continue
        shared = SequenceMatcher(None, norm, target, autojunk=False).find_longest_match().size
        if norm in target or shared >= max(4, _LINE_SHARE * len(norm)):
            keep.append(index)
    if not keep:
        return text
    return "\n".join(lines[keep[0]: keep[-1] + 1])


def load_chunks(client: QdrantClient, collection: str) -> dict[str, list[dict]]:
    by_file: dict[str, list[dict]] = defaultdict(list)
    offset = None
    while True:
        points, offset = client.scroll(
            collection, limit=1000, offset=offset,
            with_payload=["file_name", "physical_pages", "text", "element_ids", "usage_policy"],
        )
        for point in points:
            by_file[point.payload["file_name"]].append(point.payload)
        if offset is None:
            return by_file


def _best_chunk(target: str, page: int | None, chunks: list[dict]) -> tuple[dict | None, float]:
    """The chunk sharing the most evidence among those that overlap enough."""
    needed = min(MIN_SHARED, len(target) // 2)
    best, best_shared, best_overlap = None, 0, 0.0
    for window in (0, 1):
        pages = {page - window, page, page + window} if page else set()
        for chunk in chunks:
            if pages and not pages & set(chunk.get("physical_pages") or []):
                continue
            overlap, shared = _overlap(target, _normalized(chunk["text"]))
            if overlap >= MIN_OVERLAP and shared >= needed and shared > best_shared:
                best, best_shared, best_overlap = chunk, shared, overlap
        if best is not None:
            break
    return best, best_overlap


def reanchor_evidence(evidence: dict, chunks: list[dict],
                      manual: dict | None = None) -> tuple[dict, float]:
    # The clean rendering is the target when present: old quotes carry old
    # recognition errors and sometimes a neighbouring paragraph the old
    # reading order mixed in.
    target = _normalized(evidence.get("rendering") or evidence.get("quote") or "")
    page = evidence.get("page")
    if manual is not None:
        named = [c for c in chunks if c["element_ids"] == manual["element_ids"]]
        if len(named) != 1:
            raise SystemExit(f"manual anchor {manual} matches {len(named)} chunks")
        best, best_score = named[0], _overlap(target, _normalized(named[0]["text"]))[0]
    else:
        best, best_score = _best_chunk(target, page, chunks)
    updated = dict(evidence)
    pending = [item for item in evidence.get("pending") or [] if item != UNRESOLVED]
    if best is None:
        updated["pending"] = [*pending, UNRESOLVED]
        return updated, best_score
    quote = _covering_lines(target, best["text"])
    if not _quote_matches(quote, best["text"]):
        quote = best["text"]
    chunk_pages = best.get("physical_pages") or []
    updated.update({
        "quote": quote,
        "page": page if page in chunk_pages else chunk_pages[0],
        "element_ids": list(best["element_ids"]),
        "usage_policy": best["usage_policy"],
        "quote_kind": "rebuilt_element_excerpt",
        "pending": pending,
    })
    if manual:
        updated["notes"] = "\n".join(
            n for n in (evidence.get("notes"), f"【manual reanchor】{manual['note']}") if n)
    return updated, best_score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--manual", type=Path, default=None,
                        help="JSON of rows anchored by hand: case, evidence index, element_ids, note")
    args = parser.parse_args(argv)
    manual = {}
    if args.manual:
        for entry in json.loads(args.manual.read_text(encoding="utf-8"))["rows"]:
            manual[(entry["case"], entry["evidence"])] = entry

    pointer = json.loads((ROOT / "data/registry/corpus-current.json").read_text(encoding="utf-8"))
    chunks = load_chunks(QdrantClient(url=args.qdrant_url), pointer["collection_name"])
    rows = [json.loads(line) for line in args.source.read_text(encoding="utf-8").splitlines() if line.strip()]

    report = []
    for row in rows:
        evidence = []
        for index, item in enumerate(row["evidence"]):
            updated, score = reanchor_evidence(
                item, chunks.get(item["doc_file"], []), manual.get((row["id"], index)))
            evidence.append(updated)
            report.append({
                "case": row["id"], "doc_file": item["doc_file"], "must_hit": item.get("must_hit"),
                "old_page": item.get("page"), "new_page": updated.get("page"),
                "coverage": round(score, 3), "resolved": UNRESOLVED not in updated["pending"],
                "policy": updated.get("usage_policy"),
            })
        row["evidence"] = evidence
        row["anchor_corpus"] = pointer["corpus_version"]

    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    resolved = sum(r["resolved"] for r in report)
    print(f"[reanchor] {len(rows)} cases, {resolved}/{len(report)} evidence rows re-anchored "
          f"to {pointer['corpus_version']} -> {args.output}")
    for row in report:
        if not row["resolved"]:
            print(f"  unresolved {row['case']} p{row['old_page']} must_hit={row['must_hit']} "
                  f"coverage={row['coverage']} {row['doc_file'][:30]}")
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
