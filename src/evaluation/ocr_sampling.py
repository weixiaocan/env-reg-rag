"""Deterministic, document-stratified OCR text sampling.

This module prepares evidence for a later independent review.  It does not
classify OCR text as correct or change any canonical/chunk quality state.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf

from src.application.publish_state import build_chunk_manifest
from src.application.v2_chunker import chunk_document


SAMPLE_VERSION = "ocr-sample-v1"
SAMPLE_SIZE = 25
OCR_SOURCE_REASONS = frozenset({"ocr_source_review", "needs_review_source"})
ALLOWED_REASONS = OCR_SOURCE_REASONS | {"too_short"}
_DPI = 200
_MARGIN_POINTS = 8.0
_MAX_CROP_PIXELS = 12_000_000

_REVIEW_README = """# OCR 文本抽样核对表

本目录是 T2 独立核对的工作表，不包含任何 OCR 质量判定，也不改变正式语料的准入状态。`sample_sheet.jsonl` 每行是一条样本；跨页样本在 `screenshots` 中每页对应一张 200dpi 原图裁剪。截图位于被 Git 忽略的 `crops/`，可用仓库根目录下的命令重建：

```powershell
.venv\\Scripts\\python.exe scripts\\build_ocr_sample_sheet.py
```

## 判定标准

- `exact`：与原图一致（忽略空白、换行）。
- `minor`：仅标点、全半角、空格或版式差异，不影响任何数字、单位、条款号、术语与语义。
- `error`：任何数字、单位、符号、条款编号或关键词错误；错字导致词义变化；漏字或多字超过一个短语；串行或串栏导致语义错误。
- `unjudgeable`：截图不清或裁剪错误，必须记录原因。

## 文档准入门槛（仅供 T2 汇总使用）

在可判定样本中，`error` 占比不超过 `1/25`，并报告 Wilson 95% 下界。`unjudgeable` 超过 3 条的文档不作结论。Codex 与 Claude 应分别独立判定；本工作表本身不预填结论。

## 字段与校验

`chunk_text` 来自当前正式 pointer 对应 Canonical 的确定性分块结果；生成前会把完整重算 chunk stream 与已发布 `chunk_manifest.json` 比较。`page_bboxes` 是每页 `source_spans` 的并集；`screenshots[].sha256` 绑定对应 PNG。裁剪失败会写入 `failure_reason`，不会静默跳过。
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def document_seed(document_sha256: str) -> str:
    return hashlib.sha256(
        (document_sha256 + SAMPLE_VERSION).encode("ascii")
    ).hexdigest()


def _metadata(chunk: Any) -> dict[str, Any]:
    return dict(getattr(chunk, "metadata", {}) or {})


def chunk_exclusion_reasons(chunk: Any) -> list[str]:
    """Return an empty list exactly for chunks in the T1 sampling population."""
    metadata = _metadata(chunk)
    if metadata.get("usage_policy") != "source_locator_only":
        return ["usage_policy_not_source_locator_only"]

    element_types = set(metadata.get("element_types") or [])
    policy_reasons = set(metadata.get("policy_reasons") or [])
    exclusions: list[str] = []
    if element_types != {"text"}:
        non_text = sorted(element_types - {"text"})
        exclusions.extend(f"contains_{kind}_element" for kind in non_text)
        if not non_text:
            exclusions.append("not_pure_text")
    if not (policy_reasons & OCR_SOURCE_REASONS):
        exclusions.append("missing_ocr_source_reason")
    exclusions.extend(sorted(policy_reasons - ALLOWED_REASONS))
    return sorted(set(exclusions))


def split_sampling_population(
    chunks: Iterable[Any],
) -> tuple[list[Any], Counter[str]]:
    eligible: list[Any] = []
    excluded: Counter[str] = Counter()
    for chunk in chunks:
        reasons = chunk_exclusion_reasons(chunk)
        if reasons:
            excluded.update(reasons)
        else:
            eligible.append(chunk)
    return eligible, excluded


def is_scanned_document(chunks: Sequence[Any]) -> bool:
    """Identify the ten OCR-only documents without hard-coding their hashes."""
    if not chunks:
        return False
    if any(_metadata(chunk).get("usage_policy") != "source_locator_only" for chunk in chunks):
        return False
    return any(
        "ocr_source_review" in (_metadata(chunk).get("policy_reasons") or [])
        for chunk in chunks
    )


def _first_page(chunk: Any) -> int:
    pages = [int(span.physical_page) for span in getattr(chunk, "source_spans", [])]
    if not pages:
        raise ValueError(f"chunk {getattr(chunk, 'chunk_id', '<unknown>')} has no source spans")
    return min(pages)


def _stratum(page: int, low: int, high: int) -> int:
    if high <= low:
        return 0
    relative = (page - low) / (high - low + 1)
    return min(2, int(relative * 3))


def select_document_samples(
    chunks: Sequence[Any], document_sha256: str, *, limit: int = SAMPLE_SIZE
) -> list[Any]:
    """Select stable samples while reserving capacity for front/middle/back."""
    ordered = sorted(chunks, key=lambda c: (_first_page(c), str(c.chunk_id)))
    if len(ordered) <= limit:
        return ordered

    low = min(_first_page(chunk) for chunk in ordered)
    high = max(_first_page(chunk) for chunk in ordered)
    groups: dict[int, list[Any]] = defaultdict(list)
    for chunk in ordered:
        groups[_stratum(_first_page(chunk), low, high)].append(chunk)

    rng = random.Random(int(document_seed(document_sha256), 16))
    quotas = [limit // 3 + (1 if i < limit % 3 else 0) for i in range(3)]
    chosen: list[Any] = []
    for index, quota in enumerate(quotas):
        group = list(groups.get(index, []))
        rng.shuffle(group)
        chosen.extend(group[:quota])

    if len(chosen) < limit:
        chosen_ids = {chunk.chunk_id for chunk in chosen}
        remainder = [chunk for chunk in ordered if chunk.chunk_id not in chosen_ids]
        rng.shuffle(remainder)
        chosen.extend(remainder[: limit - len(chosen)])
    return sorted(chosen, key=lambda c: (_first_page(c), str(c.chunk_id)))


def union_bboxes_by_page(source_spans: Iterable[Any]) -> list[dict[str, Any]]:
    grouped: dict[int, list[list[float]]] = defaultdict(list)
    for span in source_spans:
        page = int(getattr(span, "physical_page"))
        bbox = [float(value) for value in getattr(span, "bbox")]
        if len(bbox) != 4 or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
            raise ValueError(f"invalid bbox on physical page {page}: {bbox!r}")
        grouped[page].append(bbox)
    result = []
    for page, boxes in sorted(grouped.items()):
        result.append(
            {
                "physical_page": page,
                "bbox": [
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ],
            }
        )
    return result


def sample_record(
    chunk: Any, *, document_name: str, document_sha256: str, sample_id: str
) -> dict[str, Any]:
    page_bboxes = union_bboxes_by_page(chunk.source_spans)
    screenshots = [
        {
            "physical_page": row["physical_page"],
            "path": (
                f"eval/audits/ocr_sampling/crops/{document_sha256}/"
                f"{sample_id}__p{row['physical_page']:04d}.png"
            ),
            "sha256": None,
            "failure_reason": None,
        }
        for row in page_bboxes
    ]
    return {
        "sample_id": sample_id,
        "document_name": document_name,
        "document_sha256": document_sha256,
        "chunk_id": chunk.chunk_id,
        "physical_pages": [row["physical_page"] for row in page_bboxes],
        "page_bboxes": page_bboxes,
        "chunk_text": chunk.text,
        "screenshots": screenshots,
        "blocking_reasons": list(_metadata(chunk).get("policy_reasons") or []),
    }


def _render_crop(pdf_path: Path, physical_page: int, bbox: list[float]) -> bytes:
    with pymupdf.open(pdf_path) as pdf:
        if not 1 <= physical_page <= len(pdf):
            raise ValueError(f"physical page {physical_page} is outside PDF page range")
        page = pdf[physical_page - 1]
        page_rect = page.rect
        clip = pymupdf.Rect(
            max(page_rect.x0, bbox[0] - _MARGIN_POINTS),
            max(page_rect.y0, bbox[1] - _MARGIN_POINTS),
            min(page_rect.x1, bbox[2] + _MARGIN_POINTS),
            min(page_rect.y1, bbox[3] + _MARGIN_POINTS),
        )
        scale = _DPI / 72.0
        if clip.is_empty or clip.width * scale * clip.height * scale > _MAX_CROP_PIXELS:
            raise ValueError("crop is empty or exceeds the 12,000,000 pixel limit")
        return page.get_pixmap(dpi=_DPI, clip=clip, alpha=False).tobytes("png")


def render_record_crops(root: Path, pdf_path: Path, record: dict[str, Any]) -> None:
    bbox_by_page = {
        int(row["physical_page"]): list(row["bbox"])
        for row in record["page_bboxes"]
    }
    for screenshot in record["screenshots"]:
        output = root / screenshot["path"]
        try:
            data = _render_crop(
                pdf_path,
                int(screenshot["physical_page"]),
                bbox_by_page[int(screenshot["physical_page"])],
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            screenshot["sha256"] = hashlib.sha256(data).hexdigest()
        except Exception as exc:  # failure is evidence and must remain in the sheet
            screenshot["failure_reason"] = f"{type(exc).__name__}: {exc}"


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _stable_generated_at(summary_path: Path, corpus_version: str) -> str:
    if summary_path.is_file():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            if prior.get("corpus_version") == corpus_version and prior.get("generated_at"):
                return str(prior["generated_at"])
        except (OSError, ValueError, UnicodeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def build_sample_sheet(root: Path) -> dict[str, Any]:
    pointer_path = root / "data/registry/corpus-current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    corpus_version = str(pointer["corpus_version"])
    canonical_dir = root / "data/canonical/v2" / corpus_version
    canonical_paths = sorted(canonical_dir.glob("*.canonical.json"))
    if not canonical_paths:
        raise FileNotFoundError(f"no canonical documents in {canonical_dir}")

    manifest = json.loads((root / pointer["manifest"]).read_text(encoding="utf-8"))
    assets = {str(row["sha256"]): row for row in manifest["assets"]}
    chunks_by_document: dict[str, list[Any]] = {}
    all_chunks: list[Any] = []
    for path in canonical_paths:
        canonical = json.loads(path.read_text(encoding="utf-8"))
        document_sha = str(canonical["source"]["sha256"])
        chunks = chunk_document(canonical)
        chunks_by_document[document_sha] = chunks
        all_chunks.extend(chunks)

    published_manifest = json.loads((canonical_dir / "chunk_manifest.json").read_text(encoding="utf-8"))
    recomputed_manifest = build_chunk_manifest(all_chunks)
    if recomputed_manifest != published_manifest:
        raise RuntimeError("local canonical chunk stream does not match the published chunk manifest")

    output_dir = root / "eval/audits/ocr_sampling"
    summary_path = output_dir / "sample_summary.json"
    records: list[dict[str, Any]] = []
    document_summaries: list[dict[str, Any]] = []
    crop_failures = 0
    for document_sha, chunks in sorted(chunks_by_document.items()):
        if not is_scanned_document(chunks):
            continue
        eligible, excluded = split_sampling_population(chunks)
        selected = select_document_samples(eligible, document_sha)
        asset = assets[document_sha]
        document_name = str(asset["display_file_name"])
        for index, chunk in enumerate(selected, 1):
            record = sample_record(
                chunk,
                document_name=document_name,
                document_sha256=document_sha,
                sample_id=f"ocr-{document_sha[:12]}-{index:03d}",
            )
            render_record_crops(root, root / asset["canonical_rel_path"], record)
            crop_failures += sum(bool(row["failure_reason"]) for row in record["screenshots"])
            records.append(record)
        document_summaries.append(
            {
                "document_name": document_name,
                "document_sha256": document_sha,
                "source_locator_chunk_count": len(chunks),
                "sampling_population_count": len(eligible),
                "sample_count": len(selected),
                "excluded_reason_counts": dict(sorted(excluded.items())),
                "seed": document_seed(document_sha),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "README.md").write_text(_REVIEW_README, encoding="utf-8")
    sheet_path = output_dir / "sample_sheet.jsonl"
    sheet_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in records
    )
    sheet_path.write_bytes(sheet_bytes)
    summary = {
        "schema_version": "ocr-sample-sheet-summary-v1",
        "corpus_version": corpus_version,
        "generated_at": _stable_generated_at(summary_path, corpus_version),
        "script_git_commit": _git_commit(root),
        "sample_version": SAMPLE_VERSION,
        "sample_limit_per_document": SAMPLE_SIZE,
        "document_count": len(document_summaries),
        "sample_count": len(records),
        "screenshot_count": sum(len(row["screenshots"]) for row in records),
        "crop_failure_count": crop_failures,
        "chunk_stream_verification": {
            "status": "matched_published_chunk_manifest",
            "total_chunks": recomputed_manifest["total_chunks"],
            "chunk_set_sha256": recomputed_manifest["chunk_set_sha256"],
        },
        "sample_sheet_sha256": hashlib.sha256(sheet_bytes).hexdigest(),
        "documents": document_summaries,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def verify_sample_sheet(root: Path) -> dict[str, Any]:
    """Verify worksheet counts, crop hashes, and byte-exact chunk text locally."""
    pointer = json.loads(
        (root / "data/registry/corpus-current.json").read_text(encoding="utf-8")
    )
    corpus_version = str(pointer["corpus_version"])
    canonical_dir = root / "data/canonical/v2" / corpus_version
    chunks: dict[tuple[str, str], Any] = {}
    for path in sorted(canonical_dir.glob("*.canonical.json")):
        canonical = json.loads(path.read_text(encoding="utf-8"))
        document_sha = str(canonical["source"]["sha256"])
        for chunk in chunk_document(canonical):
            chunks[(document_sha, chunk.chunk_id)] = chunk

    output_dir = root / "eval/audits/ocr_sampling"
    summary = json.loads((output_dir / "sample_summary.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (output_dir / "sample_sheet.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    expected_samples = sum(int(row["sample_count"]) for row in summary["documents"])
    if len(rows) != int(summary["sample_count"]) or len(rows) != expected_samples:
        raise ValueError("sample sheet row count does not match summary counts")
    if len({row["sample_id"] for row in rows}) != len(rows):
        raise ValueError("sample_id values are not unique")

    screenshot_count = 0
    for row in rows:
        key = (str(row["document_sha256"]), str(row["chunk_id"]))
        chunk = chunks.get(key)
        if chunk is None:
            raise ValueError(f"sample chunk is absent from current canonical stream: {key}")
        if row["chunk_text"] != chunk.text:
            raise ValueError(f"chunk text differs byte-for-byte: {row['sample_id']}")
        if chunk_exclusion_reasons(chunk):
            raise ValueError(f"sample is outside OCR population: {row['sample_id']}")
        for screenshot in row["screenshots"]:
            screenshot_count += 1
            if screenshot.get("failure_reason"):
                raise ValueError(
                    f"sample crop recorded a failure: {row['sample_id']} "
                    f"{screenshot['failure_reason']}"
                )
            path = root / screenshot["path"]
            if not path.is_file():
                raise FileNotFoundError(f"sample crop is missing: {path}")
            if sha256_file(path) != screenshot.get("sha256"):
                raise ValueError(f"sample crop hash mismatch: {path}")
    if screenshot_count != int(summary["screenshot_count"]):
        raise ValueError("screenshot count does not match summary")
    return {
        "corpus_version": corpus_version,
        "document_count": len(summary["documents"]),
        "sample_count": len(rows),
        "screenshot_count": screenshot_count,
        "chunk_text_exact_match_count": len(rows),
        "screenshot_hash_match_count": screenshot_count,
        "status": "verified",
    }
