"""Re-anchor golden_v2 onto corpus-8dcc90d4921b and write golden_v3.

golden_v2 was anchored to corpus-0b9a1fd84e3d, whose canonical files were lost
in the 2026-10-11 data-loss incident, so the old reanchor tool (hard-wired to
the two previous corpora) cannot run.  This script re-locates every evidence
row by its quote/rendering text inside the new canonical document on the same
page (+-1), then takes page, element ids and usage policy from the live chunk
that carries those elements.  Rows that cannot be located are kept with a
``pending`` marker instead of being guessed.

Read-only for corpus, Qdrant and pointer; writes only eval/golden_v3.jsonl and
a report next to this script.  No LLM calls.
"""
from __future__ import annotations

import glob
import json
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient  # noqa: E402

NEW_CORPUS = sys.argv[1] if len(sys.argv) > 1 else "corpus-8dcc90d4921b"
# Optional explicit collection (a staging collection before publish).
COLLECTION = sys.argv[2] if len(sys.argv) > 2 else None
SOURCE = ROOT / "eval" / "golden_v2.jsonl"
TARGET = ROOT / "eval" / "golden_v3.jsonl"
REPORT = Path(__file__).with_name("reanchor_report.json")
# Owner-requested replacements for cases whose source PDFs were removed.
REPLACEMENTS = Path(__file__).with_name("replacements.jsonl")
# Until 2026-10-12 the 《城市排水工程规划规范》 PDF carried the CJJ 61-2018
# file name; the alias applies only to corpora built before the rename.
DOC_ALIASES = {"城市排水工程规划规范.pdf": "《城镇排水工程设计规范》（CJJ 61-2018）.pdf"}
REMOVED_DOCS = {"GBT+47884-2026.pdf",
                "20_WD_SIDS_2026003277_供水和污水系统资产管理指南  第3部分：.pdf"}
MATCH_THRESHOLD = 0.75


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s|>]+", "", text)


def best_window_ratio(needle: str, hay: str) -> float:
    """Similarity of ``needle`` to its best-matching window of ``hay``."""
    if not needle or not hay:
        return 0.0
    if needle in hay:
        return 1.0
    matcher = SequenceMatcher(None, needle, hay, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(needle)


def load_canonical() -> dict[str, dict]:
    docs = {}
    for path in glob.glob(str(ROOT / "data" / "canonical" / "v2" / NEW_CORPUS / "*.canonical.json")):
        doc = json.load(open(path, encoding="utf-8"))
        docs[doc["source"]["file_name"]] = doc
    return docs


def load_chunks(collection: str) -> list[dict]:
    client = QdrantClient(url="http://127.0.0.1:6333", timeout=60)
    points, offset = [], None
    while True:
        batch, offset = client.scroll(
            collection, limit=1000, offset=offset, with_vectors=False,
            with_payload=["file_name", "element_ids", "usage_policy", "physical_pages", "chunk_id", "text"])
        points += [p.payload for p in batch]
        if offset is None:
            return points


def element_text(element: dict) -> str:
    content = element.get("content") or {}
    if element["type"] == "table":
        return " ".join(cell.get("text") or "" for cell in content.get("cells") or [])
    if element["type"] == "formula":
        return " ".join(filter(None, [content.get("latex"), content.get("recognized_formula"),
                                      content.get("context_text")]))
    return content.get("text") or content.get("caption") or ""


def quote_lines(evidence: dict) -> list[str]:
    """Quote lines worth matching: old heading-path prefixes ("a > b") and
    figure placeholders are projection artefacts of the old corpus."""
    lines = []
    for line in evidence["quote"].split("\n"):
        if " > " in line:
            line = line.rsplit(" > ", 1)[-1]
        if line.strip().startswith("[图") or len(norm(line)) < 4:
            continue
        lines.append(norm(line))
    return lines or [norm(evidence["quote"])]


def locate_chunk(evidence: dict, doc_file: str, chunks: list[dict]) -> tuple[dict | None, float]:
    """Fallback: the chunk on page +-1 whose text best covers the quote lines."""
    lines = quote_lines(evidence)
    best, best_cov = None, 0.0
    for chunk in chunks:
        if chunk["file_name"] != doc_file or not any(
                abs(p - evidence["page"]) <= 1 for p in chunk["physical_pages"]):
            continue
        text = norm(chunk["text"])
        cov = sum(len(line) for line in lines if best_window_ratio(line, text) >= MATCH_THRESHOLD)
        cov /= max(sum(len(line) for line in lines), 1)
        if cov > best_cov:
            best, best_cov = chunk, cov
    return best, best_cov


def locate(evidence: dict, doc: dict) -> tuple[list[str], float]:
    """Element ids on page +-1 that carry the quote lines, and the coverage."""
    page = evidence["page"]
    candidates = [e for e in doc["elements"]
                  if any(abs(s["physical_page"] - page) <= 1 for s in e.get("source_spans") or [])]
    lines = quote_lines(evidence)
    chosen: list[str] = []
    covered = 0
    for line in lines:
        scored = sorted(((best_window_ratio(line, norm(element_text(e))), e["element_id"])
                         for e in candidates), reverse=True)
        if scored and scored[0][0] >= MATCH_THRESHOLD:
            covered += len(line)
            if scored[0][1] not in chosen:
                chosen.append(scored[0][1])
    return chosen, covered / max(sum(len(line) for line in lines), 1)


def main() -> None:
    collection = COLLECTION
    if collection is None:
        pointer = json.load(open(ROOT / "data" / "registry" / "corpus-current.json", encoding="utf-8"))
        if pointer["corpus_version"] != NEW_CORPUS:
            raise SystemExit(f"pointer is {pointer['corpus_version']}, expected {NEW_CORPUS}")
        collection = pointer["collection_name"]
    elif NEW_CORPUS not in collection:
        raise SystemExit(f"collection {collection} does not belong to {NEW_CORPUS}")
    docs = load_canonical()
    chunks = load_chunks(collection)
    cases = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    replacements = {}
    if REPLACEMENTS.is_file():
        replacements = {r["id"]: r for r in map(json.loads, REPLACEMENTS.read_text(encoding="utf-8").splitlines())}
    cases = [replacements.get(case["id"], case) for case in cases]
    report, stats = [], Counter()
    for case in cases:
        for evidence in case["evidence"]:
            old = {k: evidence.get(k) for k in ("doc_file", "page", "element_ids", "usage_policy")}
            doc_file = evidence["doc_file"]
            if doc_file not in docs and DOC_ALIASES.get(doc_file) in docs:
                doc_file = DOC_ALIASES[doc_file]
            row = {"case": case["id"], "old": old}
            if evidence["doc_file"] in REMOVED_DOCS:
                evidence["element_ids"] = []
                evidence["usage_policy"] = None
                evidence["pending"] = sorted({*evidence.get("pending", []), "source_document_removed"})
                row["result"] = "source_document_removed"
            else:
                ids, coverage = locate(evidence, docs[doc_file])
                carriers = [c for c in chunks if c["file_name"] == doc_file and ids
                            and set(ids) & set(c["element_ids"])]
                if not ids or coverage < 0.6 or not carriers:
                    chunk, coverage = locate_chunk(evidence, doc_file, chunks)
                    if chunk is not None and coverage >= 0.6:
                        ids, carriers = list(chunk["element_ids"]), [chunk]
                        row["matched_by"] = "chunk_text"
                if not ids or coverage < 0.6 or not carriers:
                    evidence["pending"] = sorted({*evidence.get("pending", []), "reanchor_unresolved"})
                    row["result"] = "unresolved"
                else:
                    evidence["doc_file"] = doc_file
                    evidence["element_ids"] = ids
                    pages = sorted({p for c in carriers for p in c["physical_pages"]})
                    evidence["page"] = evidence["page"] if evidence["page"] in pages else pages[0]
                    policies = {c["usage_policy"] for c in carriers}
                    evidence["usage_policy"] = ("source_locator_only" if "source_locator_only" in policies
                                                else "answer_and_citation")
                    evidence["pending"] = [p for p in evidence.get("pending", [])
                                           if p != "reanchor_unresolved"]
                    row["result"] = "resolved"
                    row["coverage"] = round(coverage, 3)
                row["new"] = {k: evidence.get(k) for k in ("doc_file", "page", "element_ids", "usage_policy")}
            stats[row["result"]] += 1
            report.append(row)
        case["anchor_corpus"] = NEW_CORPUS
    TARGET.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    policy_shift = Counter((r["old"]["usage_policy"], r.get("new", {}).get("usage_policy"))
                           for r in report if r["result"] == "resolved")
    REPORT.write_text(json.dumps({
        "source": str(SOURCE.relative_to(ROOT)), "target": str(TARGET.relative_to(ROOT)),
        "corpus_version": NEW_CORPUS, "stats": dict(stats),
        "policy_shift": {f"{a}->{b}": n for (a, b), n in policy_shift.items()},
        "rows": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(dict(stats))
    print({f"{a}->{b}": n for (a, b), n in policy_shift.items()})


if __name__ == "__main__":
    main()
