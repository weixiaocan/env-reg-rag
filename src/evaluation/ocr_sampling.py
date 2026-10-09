"""Deterministic, document-stratified OCR text sampling.

This module prepares evidence for a later independent review.  It does not
classify OCR text as correct or change any canonical/chunk quality state.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymupdf

from src.application.publish_state import build_chunk_manifest
from src.application.v2_chunker import _format_section_path, chunk_document


SAMPLE_VERSION = "ocr-sample-v1"
SAMPLE_SIZE = 25
OCR_SOURCE_REASONS = frozenset({"ocr_source_review", "needs_review_source"})
ALLOWED_REASONS = OCR_SOURCE_REASONS | {"too_short"}
_CONTEXT_DPI = 200
_THUMBNAIL_DPI = 100
_PAGE_SIDE_MARGIN_POINTS = 24.0
_CONTEXT_VERTICAL_MARGIN_POINTS = 40.0
_MAX_CROP_PIXELS = 12_000_000

_REVIEW_README = """# OCR 文本抽样核对表

本目录是 T2 独立核对的工作表，不包含任何 OCR 质量判定，也不改变正式语料的准入状态。`sample_sheet.jsonl` 每行是一条样本；跨页样本在 `screenshots` 中每页都有上下文裁剪和红框整页缩略图。截图位于被 Git 忽略的 `crops/`，可用仓库根目录下的命令重建：

```powershell
.venv\\Scripts\\python.exe scripts\\build_ocr_sample_sheet.py
```

## 多维判定标准

- `text_fidelity`：对 `body_text` 与原图判定 `exact` / `minor` / `error` / `unjudgeable`。`exact` 允许忽略空白和换行；`minor` 仅限不影响数字、单位、条款号、术语和语义的标点、全半角、空格或版式差异；任何数字、单位、符号、条款编号或关键词错误，词义变化，漏/多超过一个短语，以及上下文图中可见的漏行、行尾截断、串行/串栏均为 `error`；截图不清或裁剪错误为 `unjudgeable` 并记录原因。
- `error_types`（可多选）：`char`、`number_unit_clause`、`truncation_or_missing`、`extra_or_order`。
- `heading_path`：对 `heading_path_text` 判定 `correct` / `wrong` / `not_applicable`。
- `standalone_usable`：判定 `yes` / `fragment`。`body_text` 脱离上下文无法构成可引用信息（如“水 □”或半句话）时为 `fragment`。

## 文档准入门槛（仅供 T2 汇总使用）

在可判定样本中，`text_fidelity=error` 占比不超过 `1/25`，并报告 Wilson 95% 下界。`unjudgeable` 超过 3 条的文档不作结论。`heading_path=wrong` 与 `standalone_usable=fragment` 比例单独报告，T2 不据此下准入结论，留给 T3 决定是否额外过滤。Codex 与 Claude 应分别独立判定；本工作表本身不预填结论。

## 字段与校验

`chunk_text` 来自当前正式 pointer 对应 Canonical 的确定性分块结果；生成前会把完整重算 chunk stream 与已发布 `chunk_manifest.json` 比较。`heading_path_text` 由 chunk metadata 的 `section_path` 按 `src/application/v2_chunker.py::_format_section_path` 的规则格式化；`body_text` 按同一前缀的实际拼接规则从 `chunk_text` 中精确去除，不使用“第一行”启发式。`page_bboxes` 是每页 `source_spans` 的并集；`context` 为 200dpi、页面左右各 24pt 内全宽、bbox 上下各扩 40pt 的上下文图；`page_thumbnail` 为 100dpi 整页红框图。每张图均绑定 SHA-256，失败会写入 `failure_reason`，不会静默跳过。
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


def split_chunk_fields(chunk: Any) -> dict[str, Any]:
    """Split structural heading context from body using the chunker contract."""
    prefix = _format_section_path(_metadata(chunk).get("section_path") or [])
    has_prefix = bool(prefix and chunk.text.startswith(prefix))
    return {
        "heading_path_text": prefix.rstrip("\n"),
        "body_text": chunk.text[len(prefix):] if has_prefix else chunk.text,
        "heading_prefix_in_chunk": has_prefix,
    }


def context_clip(
    page_rect: Sequence[float], bbox: Sequence[float]
) -> list[float]:
    """Return full body-width context with 40pt vertical expansion."""
    px0, py0, px1, py1 = (float(value) for value in page_rect)
    _, by0, _, by1 = (float(value) for value in bbox)
    left = min(px1, px0 + _PAGE_SIDE_MARGIN_POINTS)
    right = max(left, px1 - _PAGE_SIDE_MARGIN_POINTS)
    return [
        left,
        max(py0, by0 - _CONTEXT_VERTICAL_MARGIN_POINTS),
        right,
        min(py1, by1 + _CONTEXT_VERTICAL_MARGIN_POINTS),
    ]


def bbox_to_pixel_rect(
    page_rect: Sequence[float], bbox: Sequence[float], *, dpi: int
) -> list[int]:
    """Map PDF point coordinates to a clamped full-page pixmap rectangle."""
    import math

    px0, py0, px1, py1 = (float(value) for value in page_rect)
    bx0, by0, bx1, by1 = (float(value) for value in bbox)
    scale = dpi / 72.0
    width = math.ceil((px1 - px0) * scale)
    height = math.ceil((py1 - py0) * scale)
    return [
        max(0, min(width, math.floor((bx0 - px0) * scale))),
        max(0, min(height, math.floor((by0 - py0) * scale))),
        max(0, min(width, math.ceil((bx1 - px0) * scale))),
        max(0, min(height, math.ceil((by1 - py0) * scale))),
    ]


def _image_slot(path: str) -> dict[str, Any]:
    return {"path": path, "sha256": None, "failure_reason": None}


def sample_record(
    chunk: Any, *, document_name: str, document_sha256: str, sample_id: str
) -> dict[str, Any]:
    page_bboxes = union_bboxes_by_page(chunk.source_spans)
    screenshots = []
    for row in page_bboxes:
        base = (
            f"eval/audits/ocr_sampling/crops/{document_sha256}/"
            f"{sample_id}__p{row['physical_page']:04d}"
        )
        screenshots.append(
            {
                "physical_page": row["physical_page"],
                "context": _image_slot(base + "__context.png"),
                "page_thumbnail": _image_slot(base + "__page.png"),
            }
        )
    fields = split_chunk_fields(chunk)
    return {
        "sample_id": sample_id,
        "document_name": document_name,
        "document_sha256": document_sha256,
        "chunk_id": chunk.chunk_id,
        "physical_pages": [row["physical_page"] for row in page_bboxes],
        "page_bboxes": page_bboxes,
        "chunk_text": chunk.text,
        **fields,
        "screenshots": screenshots,
        "blocking_reasons": list(_metadata(chunk).get("policy_reasons") or []),
    }


def _draw_red_box(pixmap: pymupdf.Pixmap, bbox: Sequence[int]) -> None:
    x0, y0, x1, y1 = (int(value) for value in bbox)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("thumbnail bbox is empty after coordinate conversion")
    width = min(3, x1 - x0)
    height = min(3, y1 - y0)
    red = (255, 0, 0)
    pixmap.set_rect(pymupdf.IRect(x0, y0, x1, y0 + height), red)
    pixmap.set_rect(pymupdf.IRect(x0, y1 - height, x1, y1), red)
    pixmap.set_rect(pymupdf.IRect(x0, y0, x0 + width, y1), red)
    pixmap.set_rect(pymupdf.IRect(x1 - width, y0, x1, y1), red)


def _render_page_evidence(
    pdf_path: Path, physical_page: int, bbox: list[float]
) -> tuple[bytes, bytes, list[float], list[int]]:
    with pymupdf.open(pdf_path) as pdf:
        if not 1 <= physical_page <= len(pdf):
            raise ValueError(f"physical page {physical_page} is outside PDF page range")
        page = pdf[physical_page - 1]
        page_rect = page.rect
        page_box = [page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1]
        clip_box = context_clip(page_box, bbox)
        clip = pymupdf.Rect(*clip_box)
        scale = _CONTEXT_DPI / 72.0
        if clip.is_empty or clip.width * scale * clip.height * scale > _MAX_CROP_PIXELS:
            raise ValueError("crop is empty or exceeds the 12,000,000 pixel limit")
        context_data = page.get_pixmap(
            dpi=_CONTEXT_DPI, clip=clip, alpha=False
        ).tobytes("png")
        thumbnail = page.get_pixmap(dpi=_THUMBNAIL_DPI, alpha=False)
        pixel_bbox = bbox_to_pixel_rect(page_box, bbox, dpi=_THUMBNAIL_DPI)
        _draw_red_box(thumbnail, pixel_bbox)
        return context_data, thumbnail.tobytes("png"), clip_box, pixel_bbox


def render_record_crops(root: Path, pdf_path: Path, record: dict[str, Any]) -> None:
    bbox_by_page = {
        int(row["physical_page"]): list(row["bbox"])
        for row in record["page_bboxes"]
    }
    for screenshot in record["screenshots"]:
        try:
            context_data, thumbnail_data, clip_box, pixel_bbox = _render_page_evidence(
                pdf_path,
                int(screenshot["physical_page"]),
                bbox_by_page[int(screenshot["physical_page"])],
            )
            screenshot["context"]["clip_bbox"] = clip_box
            screenshot["page_thumbnail"]["span_bbox_pixels"] = pixel_bbox
            for slot, data in (
                (screenshot["context"], context_data),
                (screenshot["page_thumbnail"], thumbnail_data),
            ):
                output = root / slot["path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
                slot["sha256"] = hashlib.sha256(data).hexdigest()
        except Exception as exc:  # failure is evidence and must remain in the sheet
            reason = f"{type(exc).__name__}: {exc}"
            screenshot["context"]["failure_reason"] = reason
            screenshot["page_thumbnail"]["failure_reason"] = reason
            for slot in (screenshot["context"], screenshot["page_thumbnail"]):
                path = root / slot["path"]
                if path.is_file():
                    path.unlink()


def _remove_stale_crops(root: Path, records: Sequence[dict[str, Any]]) -> None:
    """Keep the rebuildable crop directory aligned with the current sheet."""
    crop_root = root / "eval/audits/ocr_sampling/crops"
    expected = {
        (root / slot["path"]).resolve()
        for record in records
        for screenshot in record["screenshots"]
        for slot in (screenshot["context"], screenshot["page_thumbnail"])
    }
    if not crop_root.is_dir():
        return
    for path in crop_root.rglob("*.png"):
        if path.resolve() not in expected:
            path.unlink()


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
            crop_failures += sum(
                bool(slot["failure_reason"])
                for row in record["screenshots"]
                for slot in (row["context"], row["page_thumbnail"])
            )
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
    _remove_stale_crops(root, records)
    (output_dir / "README.md").write_text(_REVIEW_README, encoding="utf-8")
    sheet_path = output_dir / "sample_sheet.jsonl"
    sheet_bytes = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in records
    )
    sheet_path.write_bytes(sheet_bytes)
    summary = {
        "schema_version": "ocr-sample-sheet-summary-v2",
        "corpus_version": corpus_version,
        "generated_at": _stable_generated_at(summary_path, corpus_version),
        "script_git_commit": _git_commit(root),
        "sample_version": SAMPLE_VERSION,
        "sample_limit_per_document": SAMPLE_SIZE,
        "document_count": len(document_summaries),
        "sample_count": len(records),
        "screenshot_count": sum(2 * len(row["screenshots"]) for row in records),
        "crop_failure_count": crop_failures,
        "sample_id_set_sha256": hashlib.sha256(
            ("\n".join(sorted(row["sample_id"] for row in records)) + "\n").encode("utf-8")
        ).hexdigest(),
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
        expected_fields = split_chunk_fields(chunk)
        if any(row.get(key) != value for key, value in expected_fields.items()):
            raise ValueError(f"split chunk fields differ: {row['sample_id']}")
        if chunk_exclusion_reasons(chunk):
            raise ValueError(f"sample is outside OCR population: {row['sample_id']}")
        for screenshot in row["screenshots"]:
            for slot_name in ("context", "page_thumbnail"):
                screenshot_count += 1
                slot = screenshot[slot_name]
                if slot.get("failure_reason"):
                    raise ValueError(
                        f"sample crop recorded a failure: {row['sample_id']} "
                        f"{slot['failure_reason']}"
                    )
                path = root / slot["path"]
                if not path.is_file():
                    raise FileNotFoundError(f"sample crop is missing: {path}")
                if sha256_file(path) != slot.get("sha256"):
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


_FIDELITY_VALUES = {"exact", "minor", "error", "unjudgeable"}
_ERROR_TYPE_VALUES = {
    "char", "number_unit_clause", "truncation_or_missing", "extra_or_order",
}
_HEADING_VALUES = {"correct", "wrong", "not_applicable"}
_STANDALONE_VALUES = {"yes", "fragment"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


_EVIDENCE_BOILERPLATE = (
    "同一红框中的对应原文",
    "对应原文含完整字符",
    "对应原文含完整",
    "红框中的对应原文",
)


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _validate_evidence(row: dict[str, Any], sample: dict[str, Any]) -> None:
    sample_id = str(row["sample_id"])
    fidelity = str(row["text_fidelity"])
    evidence = str(row["evidence"] or "").strip()
    if fidelity == "unjudgeable":
        if evidence:
            raise ValueError(f"{sample_id}: unjudgeable evidence must be empty")
        return
    if not evidence:
        raise ValueError(f"{sample_id}: {fidelity} requires evidence")
    if any(phrase in evidence for phrase in _EVIDENCE_BOILERPLATE):
        raise ValueError(f"{sample_id}: evidence contains banned boilerplate")

    candidate_text = _compact_text(str(sample.get("chunk_text") or ""))
    if fidelity == "exact":
        match = re.fullmatch(r"原图『(.{8,})』一致", evidence, flags=re.DOTALL)
        if not match:
            raise ValueError(
                f"{sample_id}: exact evidence must be 原图『<at least 8 chars>』一致"
            )
        anchor = _compact_text(match.group(1))
        if len(anchor) < 8 or anchor not in candidate_text:
            raise ValueError(f"{sample_id}: exact anchor is absent from candidate chunk")
        return

    match = re.fullmatch(
        r"OCR『(.+?)』\s*vs\s*原图『(.+?)』", evidence, flags=re.DOTALL
    )
    if not match:
        raise ValueError(
            f"{sample_id}: {fidelity} evidence must be OCR『...』 vs 原图『...』"
        )
    candidate_excerpt = _compact_text(match.group(1))
    source_excerpt = _compact_text(match.group(2))
    if not candidate_excerpt or candidate_excerpt not in candidate_text:
        raise ValueError(f"{sample_id}: OCR excerpt is absent from candidate chunk")
    if not source_excerpt:
        raise ValueError(f"{sample_id}: original-image excerpt is empty")


def validate_judgments(
    sample_sheet_path: Path,
    judgments_path: Path,
    *,
    require_complete: bool = True,
) -> dict[str, int]:
    """Validate one blind-review judgment file against its immutable worksheet."""
    samples = _read_jsonl(sample_sheet_path)
    judgments = _read_jsonl(judgments_path)
    sample_by_id = {str(row["sample_id"]): row for row in samples}
    ids = [str(row.get("sample_id", "")) for row in judgments]
    if ids != sorted(ids):
        raise ValueError("judgment rows must be sorted by sample_id")
    if len(ids) != len(set(ids)):
        raise ValueError("judgment sample_id values are not unique")
    if not set(ids) <= set(sample_by_id):
        extra = sorted(set(ids) - set(sample_by_id))
        raise ValueError(f"judgment sample_id set mismatch: extra={extra}")
    if require_complete and set(ids) != set(sample_by_id):
        missing = sorted(set(sample_by_id) - set(ids))
        raise ValueError(f"judgment sample_id set mismatch: missing={missing}, extra=[]")

    required = {
        "sample_id", "judge", "model", "text_fidelity", "error_types",
        "heading_path", "standalone_usable", "evidence", "unjudgeable_reason",
        "images_viewed", "notes",
    }
    evidence_to_id: dict[str, str] = {}
    for row in judgments:
        sample_id = str(row["sample_id"])
        missing_fields = sorted(required - set(row))
        if missing_fields:
            raise ValueError(f"{sample_id}: missing fields {missing_fields}")
        if row["judge"] != "codex" or not str(row["model"]).strip():
            raise ValueError(f"{sample_id}: judge/model is invalid")
        fidelity = row["text_fidelity"]
        if fidelity not in _FIDELITY_VALUES:
            raise ValueError(f"{sample_id}: invalid text_fidelity")
        error_types = row["error_types"]
        if not isinstance(error_types, list) or not set(error_types) <= _ERROR_TYPE_VALUES:
            raise ValueError(f"{sample_id}: invalid error_types")
        if fidelity == "error" and not error_types:
            raise ValueError(f"{sample_id}: error requires error_types")
        if fidelity != "error" and error_types:
            raise ValueError(f"{sample_id}: only error may have error_types")
        _validate_evidence(row, sample_by_id[sample_id])
        normalized_evidence = _compact_text(str(row["evidence"] or ""))
        if normalized_evidence:
            duplicate = evidence_to_id.get(normalized_evidence)
            if duplicate is not None:
                raise ValueError(f"{sample_id}: evidence duplicates sample {duplicate}")
            evidence_to_id[normalized_evidence] = sample_id
        reason = row["unjudgeable_reason"]
        if fidelity == "unjudgeable" and not (isinstance(reason, str) and reason.strip()):
            raise ValueError(f"{sample_id}: unjudgeable requires a reason")
        if fidelity != "unjudgeable" and reason is not None:
            raise ValueError(f"{sample_id}: unjudgeable_reason must be null")
        if row["heading_path"] not in _HEADING_VALUES:
            raise ValueError(f"{sample_id}: invalid heading_path")
        if row["standalone_usable"] not in _STANDALONE_VALUES:
            raise ValueError(f"{sample_id}: invalid standalone_usable")
        viewed = row["images_viewed"]
        if not isinstance(viewed, list) or len(viewed) != len(set(viewed)):
            raise ValueError(f"{sample_id}: images_viewed must be a unique list")
        expected_contexts = {
            shot["context"]["path"] for shot in sample_by_id[sample_id]["screenshots"]
        }
        if not expected_contexts <= set(viewed):
            missing = sorted(expected_contexts - set(viewed))
            raise ValueError(f"{sample_id}: context images not viewed: {missing}")
        expected_thumbnails = {
            shot["page_thumbnail"]["path"]
            for shot in sample_by_id[sample_id]["screenshots"]
        }
        if not expected_thumbnails <= set(viewed):
            missing = sorted(expected_thumbnails - set(viewed))
            raise ValueError(f"{sample_id}: page thumbnails not viewed: {missing}")
    return {"sample_count": len(samples), "judgment_count": len(judgments)}


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float | None]:
    if total == 0:
        return [None, None]
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    margin = z * ((rate * (1.0 - rate) / total + z * z / (4.0 * total * total)) ** 0.5) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def build_judgments_summary(sample_sheet_path: Path, judgments_path: Path) -> dict[str, Any]:
    validate_judgments(sample_sheet_path, judgments_path)
    samples = {str(row["sample_id"]): row for row in _read_jsonl(sample_sheet_path)}
    judgments = _read_jsonl(judgments_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in judgments:
        grouped.setdefault(str(samples[str(row["sample_id"])]["document_name"]), []).append(row)
    documents = []
    for document_name, rows in sorted(grouped.items()):
        judgeable = [row for row in rows if row["text_fidelity"] != "unjudgeable"]
        errors = sum(row["text_fidelity"] == "error" for row in judgeable)
        heading_applicable = [row for row in rows if row["heading_path"] != "not_applicable"]
        heading_wrong = sum(row["heading_path"] == "wrong" for row in heading_applicable)
        fragments = sum(row["standalone_usable"] == "fragment" for row in rows)
        documents.append({
            "document_name": document_name,
            "sample_count": len(rows),
            "judgeable_count": len(judgeable),
            "unjudgeable_count": len(rows) - len(judgeable),
            "error_count": errors,
            "error_rate": errors / len(judgeable) if judgeable else None,
            "error_rate_wilson_95": wilson_interval(errors, len(judgeable)),
            "heading_applicable_count": len(heading_applicable),
            "heading_wrong_count": heading_wrong,
            "heading_wrong_rate": heading_wrong / len(heading_applicable) if heading_applicable else None,
            "fragment_count": fragments,
            "fragment_rate": fragments / len(rows),
        })
    return {
        "schema_version": "ocr-judgments-summary-v1",
        "judge": "codex",
        "sample_count": len(judgments),
        "documents": documents,
    }
