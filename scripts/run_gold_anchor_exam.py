"""Grade a corpus version (or an exported chunk set) on the human gold anchors.

Usage:
    python scripts/run_gold_anchor_exam.py --corpus-version corpus-0b9a1fd84e3d
    python scripts/run_gold_anchor_exam.py --chunks-jsonl path/to/chunks.jsonl

``--chunks-jsonl`` rows need ``document`` (file name), ``pages`` (list of
physical pages), ``section_labels`` (list) or ``section_path`` ("a > b"), and
``body``. Read-only: never writes corpus artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from src.evaluation.gold_anchor_exam import (  # noqa: E402
    ExamChunk, chunks_from_v2, grade, normalize_label, structure_label,
)

GOLD = ROOT / "data/registry/m2-anchor-review-v0.json"


def _chunks_from_corpus(version: str) -> list[ExamChunk]:
    from src.application.v2_chunker import chunk_document

    out: list[ExamChunk] = []
    for path in sorted((ROOT / "data/canonical/v2" / version).glob("*.canonical.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        name = (doc.get("source") or {}).get("file_name") or path.name
        out.extend(chunks_from_v2(name, chunk_document(doc)))
    return out


def _chunks_from_jsonl(path: Path) -> list[ExamChunk]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels = row.get("section_labels")
        if labels is None:
            nodes = [n for n in str(row.get("section_path") or "").split(">") if n.strip()]
            labels = [structure_label(n) or "" for n in nodes]
        out.append(ExamChunk(
            document=str(row["document"]),
            pages=frozenset(int(p) for p in row.get("pages") or []),
            section_labels=tuple(normalize_label(x) for x in labels),
            body=str(row.get("body") or ""),
        ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--corpus-version")
    source.add_argument("--chunks-jsonl", type=Path)
    parser.add_argument("--only-document", help="restrict anchors to file names containing this")
    parser.add_argument("--output", type=Path, help="write full JSON report here")
    args = parser.parse_args()

    anchors = json.loads(GOLD.read_text(encoding="utf-8"))["anchors"]
    if args.only_document:
        anchors = [a for a in anchors if args.only_document in a["file_name"]]
    chunks = (_chunks_from_corpus(args.corpus_version) if args.corpus_version
              else _chunks_from_jsonl(args.chunks_jsonl))
    report = grade(anchors, chunks)
    payload = {
        "source": args.corpus_version or str(args.chunks_jsonl),
        "summary": report.summary(),
        "results": [r.__dict__ for r in report.results],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    for r in report.results:
        if not r.text_pass or r.structure_pass is False:
            print(f"  FAIL {r.anchor_id} p{r.pdf_page} text={r.text_pass} "
                  f"structure={r.structure_pass} section={r.matched_section} {r.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
