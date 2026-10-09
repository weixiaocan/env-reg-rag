"""Write a content-addressed, read-only diff from published to Canonical candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"immutable comparison conflict: {path}")
        return
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _load_pi(manifest: dict[str, Any]) -> tuple[Path, dict[str, dict[str, Any]]]:
    path = ROOT / str(manifest["canonical_documents"])
    docs = {str(row["sha256"]): row for row in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)}
    return path, docs


def _semantic_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": page.get("text"),
        "elements": [{k: row.get(k) for k in ("type", "text", "bbox", "reading_order")}
                     for row in page.get("elements") or []],
        "regions": [{k: row.get(k) for k in
                     ("region_id", "kind", "bbox", "text", "latex", "execution_status",
                      "quality_status", "issues", "relations")}
                    for row in page.get("regions") or []],
        "tables": page.get("tables") or [],
    }


def _canonical(version: str) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted((ROOT / "data/canonical/v2" / version).glob("*.canonical.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[str(row["source"]["sha256"])] = row
    return result


def _element_signature(element: dict[str, Any]) -> str:
    return _stable({
        "type": element.get("type"), "role": element.get("role"),
        "content": element.get("content"), "source_spans": element.get("source_spans"),
        "links": element.get("links"),
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    pointer_path = ROOT / "data/registry/corpus-current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    published = str(pointer["corpus_version"])
    old_manifest_path = ROOT / "data/registry" / f"{published}.json"
    new_manifest_path = ROOT / "data/registry" / f"{args.candidate}.json"
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    new_manifest = json.loads(new_manifest_path.read_text(encoding="utf-8"))
    old_pi_path, old_docs = _load_pi(old_manifest)
    new_pi_path, new_docs = _load_pi(new_manifest)
    old_can, new_can = _canonical(published), _canonical(args.candidate)

    changed_pages = []
    for sha in sorted(set(old_docs) & set(new_docs)):
        old_pages = {int(p["physical_page"]): p for p in old_docs[sha].get("pages") or []}
        new_pages = {int(p["physical_page"]): p for p in new_docs[sha].get("pages") or []}
        for number in sorted(set(old_pages) | set(new_pages)):
            before = old_pages.get(number)
            after = new_pages.get(number)
            if before is None or after is None or _stable(_semantic_page(before)) != _stable(_semantic_page(after)):
                changed_pages.append({
                    "file_sha256": sha,
                    "file_name": (new_docs.get(sha) or old_docs[sha]).get("file_name"),
                    "physical_page": number,
                    "before_region_counts": Counter(r.get("kind") for r in (before or {}).get("regions") or []),
                    "after_region_counts": Counter(r.get("kind") for r in (after or {}).get("regions") or []),
                    "before_rotation": (before or {}).get("rotation"),
                    "after_rotation": (after or {}).get("rotation"),
                })

    old_elements = [e for doc in old_can.values() for e in doc.get("elements") or []]
    new_elements = [e for doc in new_can.values() for e in doc.get("elements") or []]
    old_signatures = Counter(_element_signature(e) for e in old_elements)
    new_signatures = Counter(_element_signature(e) for e in new_elements)
    added = list((new_signatures - old_signatures).elements())
    removed = list((old_signatures - new_signatures).elements())
    report = {
        "schema_version": "canonical-candidate-diff-v1",
        "published_pointer": {
            "path": "data/registry/corpus-current.json", "sha256": _sha(pointer_path),
            "corpus_version": published, "unchanged_by_this_command": True,
        },
        "candidate_version": args.candidate,
        "input_hashes": {
            "published_manifest": _sha(old_manifest_path), "candidate_manifest": _sha(new_manifest_path),
            "published_page_intermediate": _sha(old_pi_path),
            "candidate_page_intermediate": _sha(new_pi_path),
        },
        "document_counts": {"published": len(old_docs), "candidate": len(new_docs)},
        "page_counts": {"published": int(old_manifest["page_count"]),
                        "candidate": int(new_manifest["page_count"])},
        "source_set": {
            "added_sha256": sorted(set(new_docs) - set(old_docs)),
            "removed_sha256": sorted(set(old_docs) - set(new_docs)),
            "unchanged_sha256_count": len(set(old_docs) & set(new_docs)),
        },
        "changed_semantic_pages": changed_pages,
        "changed_semantic_page_count": len(changed_pages),
        "canonical": {
            "document_counts": {"published": len(old_can), "candidate": len(new_can)},
            "element_counts": {"published": len(old_elements), "candidate": len(new_elements)},
            "element_counts_by_type": {
                "published": dict(sorted(Counter(e.get("type") for e in old_elements).items())),
                "candidate": dict(sorted(Counter(e.get("type") for e in new_elements).items())),
            },
            "semantic_element_signatures_added": len(added),
            "semantic_element_signatures_removed": len(removed),
            "added_examples": [json.loads(value) for value in added[:20]],
            "removed_examples": [json.loads(value) for value in removed[:20]],
        },
        "scope_exclusions": ["chunks", "embeddings", "Qdrant", "publish_pointer_switch",
                             "retrieval_generation_evaluation", "golden_set"],
    }
    identity = hashlib.sha256(_stable(report).encode("utf-8")).hexdigest()
    out = ROOT / "data/quality" / args.candidate / f"comparison-{identity[:16]}"
    json_data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _atomic(out / "candidate_diff.json", json_data)
    types_old = report["canonical"]["element_counts_by_type"]["published"]
    types_new = report["canonical"]["element_counts_by_type"]["candidate"]
    lines = [
        f"# Canonical 候选差异：{published} → {args.candidate}", "",
        f"- 当前发布指针 SHA-256：`{report['published_pointer']['sha256']}`（本命令只读，未修改）",
        f"- 文档：{len(old_docs)} → {len(new_docs)}；物理页：{old_manifest['page_count']} → {new_manifest['page_count']}",
        f"- 源 PDF 集合：新增 {len(report['source_set']['added_sha256'])}，移除 {len(report['source_set']['removed_sha256'])}",
        f"- Canonical 元素：{len(old_elements)} → {len(new_elements)}",
        f"- 类型：`{json.dumps(types_old, ensure_ascii=False)}` → `{json.dumps(types_new, ensure_ascii=False)}`",
        f"- 语义页面变化：{len(changed_pages)} 页",
        f"- 语义元素签名：新增 {len(added)}，移除 {len(removed)}", "",
        "## 变化页面", "",
    ]
    lines.extend(f"- `{row['file_name']}` p{row['physical_page']}：{dict(row['before_region_counts'])} → {dict(row['after_region_counts'])}"
                 for row in changed_pages)
    lines.extend(["", "## 明确未执行", "", "- " + "\n- ".join(report["scope_exclusions"]), ""])
    _atomic(out / "candidate_diff.md", "\n".join(lines).encode("utf-8"))
    print(json.dumps({"output": out.relative_to(ROOT).as_posix(),
                      "changed_semantic_page_count": len(changed_pages),
                      "canonical_element_counts": report["canonical"]["element_counts"],
                      "element_counts_by_type": report["canonical"]["element_counts_by_type"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
