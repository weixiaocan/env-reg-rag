"""Deterministic alignment and calibrated fusion for independent OCR lines.

The independent run is an existing, local verification artifact.  This module
never invokes an OCR engine.  It validates the run/page identity, aligns its
line boxes to page-intermediate text elements, and applies the deliberately
conservative T5 fusion rule only to OCR-routed body text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any


FUSION_RULE_VERSION = "t5-calibrated-v1"
_WATERMARK_TERMS = (
    "住房城乡建设部", "浏览专用", "信息公开", "内部资料",
    "仅供浏览", "仅供阅读",
)
_PUNCT_RE = re.compile(r"[\s,，。;；:：、()（）\[\]【】·•~～\-—_]+")


def normalize_ocr_text(text: str) -> str:
    """Normalize only comparison-irrelevant spacing and punctuation."""
    return _PUNCT_RE.sub("", unicodedata.normalize("NFKC", text or "")).lower()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_independent_ocr_run(run_dir: str) -> tuple[dict[str, Any], str]:
    """Load and hash a local run manifest, failing loudly when absent/invalid."""
    manifest_path = os.path.join(run_dir, "run_manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"independent OCR run manifest not found: {manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if manifest.get("schema_version") != "independent-ocr-run-v1":
        raise ValueError(f"unsupported independent OCR run schema: {manifest.get('schema_version')!r}")
    run_id = str(manifest.get("run_id") or "")
    if not run_id or os.path.basename(os.path.normpath(run_dir)) != run_id:
        raise ValueError("independent OCR run_id does not match its directory")
    return manifest, sha256_file(manifest_path)


def load_independent_ocr_page(
    run_dir: str,
    file_sha256: str,
    physical_page: int,
    *,
    page_width: float | None = None,
    page_height: float | None = None,
) -> dict[str, Any]:
    """Load one page and verify file/page identity plus point-space geometry."""
    path = os.path.join(run_dir, "pages", file_sha256, f"p{physical_page:04d}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"independent OCR page not found: sha={file_sha256} page={physical_page} path={path}")
    with open(path, "r", encoding="utf-8") as fh:
        page = json.load(fh)
    if page.get("file_sha256") != file_sha256:
        raise ValueError(f"independent OCR page file_sha256 mismatch: {path}")
    if int(page.get("physical_page") or 0) != physical_page:
        raise ValueError(f"independent OCR physical_page mismatch: {path}")
    render = page.get("render") or {}
    scale = float(render.get("scale") or 0.0)
    if page_width and scale > 0:
        rendered_width = float(render.get("width_px") or 0.0) / scale
        if abs(rendered_width - page_width) > max(2.0, page_width * 0.01):
            raise ValueError(
                f"independent OCR page width mismatch: {rendered_width} != {page_width} ({path})")
    if page_height and scale > 0:
        rendered_height = float(render.get("height_px") or 0.0) / scale
        if abs(rendered_height - page_height) > max(2.0, page_height * 0.01):
            raise ValueError(
                f"independent OCR page height mismatch: {rendered_height} != {page_height} ({path})")
    for line in page.get("lines") or []:
        bbox = _bbox(line.get("bbox"))
        if bbox is None:
            raise ValueError(f"invalid independent OCR bbox in {path}: {line.get('line_id')}")
        if page_width and bbox[2] > page_width + 2.0:
            raise ValueError(f"independent OCR bbox exceeds page width in {path}")
        if page_height and bbox[3] > page_height + 2.0:
            raise ValueError(f"independent OCR bbox exceeds page height in {path}")
    return page


def classify_noise(text: str, score: float | None) -> str | None:
    """Return a specific noise reason, or ``None`` for a usable line."""
    compact = normalize_ocr_text(text)
    if not compact:
        return "empty_after_normalization"
    if any(term in compact for term in _WATERMARK_TERMS):
        return "known_watermark_text"
    if len(compact) == 1:
        return "single_character"
    if score is None or score < 0.95:
        return "score_below_0.95"
    return None


def _bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        b = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    return b if b[0] <= b[2] and b[1] <= b[3] else None


def _intersection(a: list[float], b: list[float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _alignment_score(primary: dict[str, Any], pp: dict[str, Any]) -> float | None:
    a, b = _bbox(primary.get("bbox")), _bbox(pp.get("bbox"))
    if a is None or b is None:
        return None
    ah, bh = max(0.1, a[3] - a[1]), max(0.1, b[3] - b[1])
    vertical = max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / min(ah, bh)
    centre_delta = abs((a[1] + a[3] - b[1] - b[3]) / 2.0)
    if vertical < 0.25 and centre_delta > 0.65 * max(ah, bh):
        return None
    aw, bw = max(0.1, a[2] - a[0]), max(0.1, b[2] - b[0])
    horizontal = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / min(aw, bw)
    if horizontal < 0.08:
        return None
    similarity = SequenceMatcher(
        None, normalize_ocr_text(primary.get("text") or ""),
        normalize_ocr_text(pp.get("text") or ""), autojunk=False,
    ).ratio()
    centre = max(0.0, 1.0 - centre_delta / max(ah, bh))
    return round(0.45 * vertical + 0.2 * horizontal + 0.2 * centre + 0.15 * similarity, 6)


def classify_region(bbox: list[float], regions: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Classify a line against layout regions, preferring structured regions."""
    matches: list[tuple[str, str]] = []
    denom = max(_area(bbox), 0.1)
    for region in regions:
        rb = _bbox(region.get("bbox"))
        if rb is None or _intersection(bbox, rb) / denom < 0.5:
            continue
        kind = str(region.get("kind") or "")
        matches.append((kind, str(region.get("region_id") or "")))
    structured = [(k, rid) for k, rid in matches if k in {"table", "formula", "image"}]
    if structured:
        return "structured", [rid for _, rid in structured if rid]
    text = [rid for k, rid in matches if k == "text" and rid]
    if text:
        return "text", text
    return "none", []


def align_page(
    primary_elements: list[dict[str, Any]],
    pp_lines: list[dict[str, Any]],
    regions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic one-to-one alignment classifications."""
    primary = [e for e in primary_elements if e.get("type") == "text" and _bbox(e.get("bbox"))]
    pp = [line for line in pp_lines if _bbox(line.get("bbox"))]
    candidates: list[tuple[float, int, int]] = []
    for pi, elem in enumerate(primary):
        for li, line in enumerate(pp):
            score = _alignment_score(elem, line)
            if score is not None and score >= 0.45:
                candidates.append((score, pi, li))
    used_primary: set[int] = set()
    used_pp: set[int] = set()
    aligned: list[dict[str, Any]] = []
    for score, pi, li in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
        if pi in used_primary or li in used_pp:
            continue
        used_primary.add(pi)
        used_pp.add(li)
        elem, line = primary[pi], pp[li]
        same = normalize_ocr_text(elem.get("text") or "") == normalize_ocr_text(line.get("text") or "")
        region_class, region_ids = classify_region(_bbox(line["bbox"]), regions)
        aligned.append({
            "classification": "same" if same else "differ",
            "primary": elem,
            "pp": line,
            "alignment_score": score,
            "region_class": region_class,
            "region_ids": region_ids,
            "noise_reason": classify_noise(line.get("text") or "", line.get("score")),
        })
    for li, line in enumerate(pp):
        if li in used_pp:
            continue
        bbox = _bbox(line["bbox"])
        region_class, region_ids = classify_region(bbox, regions)
        aligned.append({
            "classification": "pp_only", "primary": None, "pp": line,
            "alignment_score": None, "region_class": region_class,
            "region_ids": region_ids,
            "noise_reason": classify_noise(line.get("text") or "", line.get("score")),
        })
    for pi, elem in enumerate(primary):
        if pi not in used_primary:
            aligned.append({
                "classification": "primary_only", "primary": elem, "pp": None,
                "alignment_score": None, "region_class": None,
                "region_ids": [], "noise_reason": None,
            })
    return sorted(aligned, key=lambda r: (
        ((_bbox((r.get("pp") or r.get("primary") or {}).get("bbox")) or [0, 0, 0, 0])[1]),
        ((_bbox((r.get("pp") or r.get("primary") or {}).get("bbox")) or [0, 0, 0, 0])[0]),
        r["classification"],
    ))


def fuse_page_text(
    page_row: dict[str, Any],
    independent_page: dict[str, Any],
    *,
    run_id: str,
    run_manifest_sha256: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Apply T5's conservative rule to one OCR page and return a deep copy."""
    page = deepcopy(page_row)
    if page.get("extraction_route") not in {"full_ocr", "hybrid"}:
        return page, {"inserted": 0, "replaced": 0}
    rows = align_page(page.get("elements") or [], independent_page.get("lines") or [], page.get("regions") or [])
    primary_by_id = {id(r["primary"]): r for r in rows if r.get("primary") is not None}
    output: list[dict[str, Any]] = []
    replaced = 0
    for elem in page.get("elements") or []:
        row = primary_by_id.get(id(elem))
        clone = deepcopy(elem)
        # Calibrated replacement is intentionally limited to high-confidence
        # aligned lines in body text regions where PP has more information.
        if row and fusion_action(row) == "replace":
            original = clone.get("text") or ""
            clone["text"] = row["pp"].get("text") or ""
            clone["normalized_text"] = clone["text"]
            clone["_ocr_fusion"] = _fusion_record(
                "replace", run_id, run_manifest_sha256, row, original)
            replaced += 1
        output.append(clone)
    inserted = 0
    for row in rows:
        if fusion_action(row) != "insert":
            continue
        pp = row["pp"]
        output.append({
            "element_id": f"p{int(page.get('page_index') or 0):04d}-ocr-fusion-{pp.get('line_id')}",
            "type": "text", "text": pp.get("text") or "",
            "normalized_text": pp.get("text") or "",
            "page_index": page.get("page_index"), "bbox": list(pp["bbox"]),
            "coordinate_origin": "top_left", "reading_order": None,
            "heading_path": [], "clause_path": [], "parent_id": None,
            "children_ids": [], "confidence": pp.get("score"),
            "source_block_id": None,
            "_ocr_fusion": _fusion_record(
                "insert", run_id, run_manifest_sha256, row, None),
        })
        inserted += 1
    output.sort(key=lambda e: (
        (_bbox(e.get("bbox")) or [0, 0, 0, 0])[1],
        (_bbox(e.get("bbox")) or [0, 0, 0, 0])[0],
        str(e.get("element_id") or ""),
    ))
    for idx, elem in enumerate(output):
        elem["reading_order"] = idx
    page["elements"] = output
    return page, {"inserted": inserted, "replaced": replaced}


def _width_ratio(primary_bbox: list[float], pp_bbox: list[float]) -> float:
    primary_width = max(0.1, float(primary_bbox[2]) - float(primary_bbox[0]))
    pp_width = max(0.1, float(pp_bbox[2]) - float(pp_bbox[0]))
    return pp_width / primary_width


def _has_textual_gain(primary_text: str, pp_text: str) -> bool:
    """Forbid information loss; allow calibrated equal-length corrections."""
    meaningful = lambda s: "".join(
        ch.lower() for ch in unicodedata.normalize("NFKC", s or "")
        if ch.isalnum() or "一" <= ch <= "鿿"
    )
    primary = meaningful(primary_text)
    independent = meaningful(pp_text)
    return independent != primary and len(independent) >= len(primary)


def fusion_action(aligned: dict[str, Any]) -> str | None:
    """Return the calibrated action for one alignment row, if any."""
    pp = aligned.get("pp") or {}
    if aligned.get("region_class") != "text" or aligned.get("noise_reason") is not None:
        return None
    if float(pp.get("score") or 0.0) < 0.97:
        return None
    if aligned.get("classification") == "pp_only":
        return "insert"
    primary = aligned.get("primary") or {}
    if (aligned.get("classification") == "differ"
            and float(aligned.get("alignment_score") or 0.0) >= 0.72
            and _has_textual_gain(primary.get("text") or "", pp.get("text") or "")
            and _width_ratio(primary.get("bbox") or [0, 0, 0, 0],
                             pp.get("bbox") or [0, 0, 0, 0]) <= 1.25):
        return "replace"
    return None


def fuse_document_pages(
    page_document: dict[str, Any],
    *,
    run_dir: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fuse every OCR-routed page; any missing run/page is a hard failure."""
    manifest, manifest_sha = load_independent_ocr_run(run_dir)
    run_id = str(manifest["run_id"])
    doc = deepcopy(page_document)
    sha = str(doc.get("sha256") or "").lower()
    counts = {"inserted": 0, "replaced": 0}
    page_count = 0
    for idx, page in enumerate(doc.get("pages") or []):
        if page.get("extraction_route") not in {"full_ocr", "hybrid"}:
            continue
        pno = int(page.get("physical_page") or idx + 1)
        independent_page = load_independent_ocr_page(
            run_dir, sha, pno,
            page_width=float(page.get("width") or 0.0),
            page_height=float(page.get("height") or 0.0),
        )
        fused, page_counts = fuse_page_text(
            page, independent_page, run_id=run_id,
            run_manifest_sha256=manifest_sha,
        )
        doc["pages"][idx] = fused
        page_count += 1
        for key in counts:
            counts[key] += page_counts[key]
    return doc, {
        "run_id": run_id,
        "run_manifest_sha256": manifest_sha,
        "rule_version": FUSION_RULE_VERSION,
        "ocr_page_count": page_count,
        **counts,
    }


def _fusion_record(
    action: str,
    run_id: str,
    manifest_sha256: str,
    aligned: dict[str, Any],
    original_text: str | None,
) -> dict[str, Any]:
    pp = aligned["pp"]
    return {
        "action": action,
        "engine": "RapidOCR/PP-OCRv6_rec_small",
        "run_id": run_id,
        "run_manifest_sha256": manifest_sha256,
        "rule_version": FUSION_RULE_VERSION,
        "original_primary_text": original_text,
        "independent_text": pp.get("text") or "",
        "independent_line_id": pp.get("line_id"),
        "independent_score": pp.get("score"),
        "alignment_score": aligned.get("alignment_score"),
        "source_region_ids": list(aligned.get("region_ids") or []),
    }
