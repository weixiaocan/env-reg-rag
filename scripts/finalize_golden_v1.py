"""Finalize the golden v1 draft against the currently published Canonical corpus.

The script performs only deterministic local work.  It canonicalizes document
names, translates the draft modality vocabulary, and binds every evidence row
to page-local V2 element IDs.  It never calls an LLM or changes the corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "eval" / "golden_v1.draft.jsonl"
DEFAULT_OUTPUT = ROOT / "eval" / "golden_v1.jsonl"

MODALITY_MAP = {
    "text": "正文",
    "table": "表格",
    "formula": "公式",
    "figure": "图片图注",
    "正文": "正文",
    "表格": "表格",
    "公式": "公式",
    "图片图注": "图片图注",
}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _element_text(element: dict[str, Any]) -> str:
    content = element.get("content") or {}
    kind = element.get("type")
    if kind == "text":
        return content.get("text") or ""
    if kind == "table":
        parts = [content.get("caption") or ""]
        parts.extend(cell.get("text") or "" for cell in content.get("cells") or [])
        parts.extend(note.get("text") or "" for note in content.get("notes") or [])
        return " ".join(parts)
    if kind == "formula":
        return " ".join(
            str(content.get(name) or "")
            for name in ("formula_number", "latex", "recognized_formula", "context_text")
        )
    if kind == "figure":
        parts = [content.get("caption") or ""]
        parts.extend(ref.get("text") or "" for ref in content.get("references") or [])
        return " ".join(parts)
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _published_documents() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    current = _load_json(ROOT / "data" / "registry" / "corpus-current.json")
    manifest = _load_json(ROOT / current["manifest"])
    canonical_dir = ROOT / "data" / "canonical" / "v2" / current["corpus_version"]

    documents_by_sha: dict[str, dict[str, Any]] = {}
    for path in canonical_dir.glob("*.canonical.json"):
        document = _load_json(path)
        documents_by_sha[document["source"]["sha256"]] = document

    aliases: dict[str, str] = {}
    for asset in manifest.get("assets") or []:
        sha = asset["sha256"]
        names = {asset.get("display_file_name") or ""}
        canonical_rel_path = asset.get("canonical_rel_path")
        if canonical_rel_path:
            names.add(Path(canonical_rel_path).name)
        names.update(
            source.get("file_name") or ""
            for source in asset.get("source_files") or []
        )
        document = documents_by_sha.get(sha)
        if document is not None:
            names.add(document["source"]["file_name"])
        for name in names:
            if name:
                aliases[name] = sha

    return aliases, documents_by_sha


def _on_page(element: dict[str, Any], physical_page: int) -> bool:
    return any(
        span.get("physical_page") == physical_page
        for span in element.get("source_spans") or []
    )


def _locate_elements(
    *,
    case_id: str,
    quote: str,
    page: int,
    document: dict[str, Any],
    must_hit: bool,
) -> tuple[str, list[str]]:
    candidates = [
        (element, _element_text(element))
        for element in document.get("elements") or []
        if _on_page(element, page) and _element_text(element).strip()
    ]
    if not candidates:
        raise ValueError(f"{case_id}: no projectable Canonical element on page {page}")

    if not must_hit:
        # Refusal cases carry a real page excerpt, never explanatory pseudo-quote text.
        excerpt_parts: list[str] = []
        excerpt_ids: list[str] = []
        for element, text in candidates:
            excerpt_parts.append(text.strip())
            excerpt_ids.append(element["element_id"])
            if len(_normalized(" ".join(excerpt_parts))) >= 40:
                break
        return " ".join(excerpt_parts), excerpt_ids

    normalized_quote = _normalized(quote)
    ranked: list[tuple[int, float, dict[str, Any], str]] = []
    for element, text in candidates:
        normalized_text = _normalized(text)
        if not normalized_text:
            continue
        longest = SequenceMatcher(
            None, normalized_quote, normalized_text, autojunk=False
        ).find_longest_match().size
        containment = (
            normalized_text in normalized_quote or normalized_quote in normalized_text
        )
        ratio = longest / max(1, min(len(normalized_quote), len(normalized_text)))
        ranked.append((longest, ratio + (1.0 if containment else 0.0), element, text))

    selected = [
        item for item in ranked
        if item[0] >= 8 and (item[1] >= 0.28 or item[0] >= 18)
    ]
    selected.sort(key=lambda item: (item[1], item[0]), reverse=True)

    if not selected:
        raise ValueError(
            f"{case_id}: quote cannot be localized in {document['source']['file_name']} "
            f"page {page}"
        )

    # Keep the locator narrow.  Long page-wide draft quotes can overlap many
    # fragments, but the best six elements are sufficient for deterministic
    # page and region traceback.
    selected = selected[:6]
    return quote, [item[2]["element_id"] for item in selected]


def finalize(source: Path, output: Path, *, status: str) -> None:
    rows = _load_rows(source)
    expected_ids = [f"G{i:03d}" for i in range(1, 51)]
    if [row.get("id") for row in rows] != expected_ids:
        raise ValueError("golden draft must contain exactly G001-G050 in order")

    aliases, documents_by_sha = _published_documents()
    for row in rows:
        row["status"] = status
        labels = row.get("labels") or {}
        if "modality" in labels:
            try:
                labels["modality"] = [MODALITY_MAP[value] for value in labels["modality"]]
            except KeyError as exc:
                raise ValueError(f"{row['id']}: unsupported modality {exc.args[0]}") from exc

        canonical_coverage: list[str] = []
        for name in row.get("doc_coverage") or []:
            if name not in aliases:
                raise ValueError(f"{row['id']}: unknown coverage document {name}")
            document = documents_by_sha[aliases[name]]
            canonical_name = document["source"]["file_name"]
            if canonical_name not in canonical_coverage:
                canonical_coverage.append(canonical_name)
        row["doc_coverage"] = canonical_coverage

        for evidence in row.get("evidence") or []:
            original_name = evidence["doc_file"]
            if original_name not in aliases:
                raise ValueError(f"{row['id']}: unknown evidence document {original_name}")
            document = documents_by_sha[aliases[original_name]]
            evidence["doc_file"] = document["source"]["file_name"]
            page = evidence.get("page")
            if not isinstance(page, int) or page < 1:
                raise ValueError(f"{row['id']}: evidence page is required")
            quote, element_ids = _locate_elements(
                case_id=row["id"],
                quote=evidence["quote"],
                page=page,
                document=document,
                must_hit=evidence.get("must_hit", True),
            )
            evidence["quote"] = quote
            evidence["element_ids"] = element_ids

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--status", choices=("reviewed", "frozen"), default="reviewed")
    args = parser.parse_args()
    finalize(args.source, args.output, status=args.status)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
