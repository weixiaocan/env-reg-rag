"""Build a source-object quality ledger for PDF -> page-intermediate -> Canonical.

The ledger is deliberately conservative.  Structural closure and byte-level
asset checks are not content approval.  ``verified_passed`` is emitted only
when a native text object is independently found in the source PDF text layer
and the Canonical projection preserves that text.  OCR and structured content
remain ``manual_review_required`` until a stronger, object-scoped check is
recorded.  Unsupported and failed processing are explicit conclusions, never
silent omissions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import pymupdf

from src.evaluation.unified_pdf_audit import original_page_crop_hashes
from src.evaluation.independent_ocr_verifier import line_belongs_to_region, strict_text


SCHEMA_VERSION = "canonical-quality-ledger-v1"
OBJECT_TYPES = (
    "source_document",
    "source_page",
    "page_content_type",
    "source_region",
    "canonical_element",
    "source_signal",
)
CONTENT_TYPES = ("text", "table", "formula", "image")
TERMINAL_EXECUTION = {"completed", "failed", "unsupported", "not_detected"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json_sha(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(data.encode("utf-8"))


def _normalise_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).strip()


def _bbox_valid(value: Any, width: float, height: float) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        return False
    x0, y0, x1, y1 = map(float, value)
    return 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height


def _content_text(element: dict[str, Any]) -> str:
    content = element.get("content") or {}
    if element.get("type") == "text":
        return str(content.get("text") or "")
    if element.get("type") == "table":
        return "\n".join(str(c.get("text") or "") for c in content.get("cells") or [])
    if element.get("type") == "formula":
        return str(content.get("latex") or content.get("recognized_formula") or "")
    if element.get("type") == "figure":
        return str(content.get("caption") or "")
    return ""


def _table_cells_signature(cells: Iterable[dict[str, Any]], *, canonical: bool = False) -> list[tuple[Any, ...]]:
    return sorted((
        int(cell.get("row") or 0),
        int(cell.get("column") if canonical else cell.get("col") or 0),
        int(cell.get("row_span") or 1),
        int(cell.get("column_span") if canonical else cell.get("col_span") or 1),
        _normalise_text(cell.get("text")),
    ) for cell in cells)


def _verify_native_table_region(pdf_path: Path, physical_page: int,
                                region: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Strict full-cell comparison against the independent native PDF layer."""
    tables = region.get("tables") or []
    details: dict[str, Any] = {"table_count": len(tables), "cell_count": 0,
                               "matched_cell_count": 0, "mismatches": []}
    if not tables:
        details["mismatches"].append("no_table_result")
        return False, details
    with pymupdf.open(pdf_path) as pdf:
        page = pdf[physical_page - 1]
        page_text = _normalise_text(page.get_text("text"))
        for table_index, table in enumerate(tables):
            cells = table.get("cells") or []
            if not cells:
                details["mismatches"].append(f"table_{table_index}:no_cells")
                continue
            occupied: set[tuple[int, int]] = set()
            for cell_index, cell in enumerate(cells):
                details["cell_count"] += 1
                row, col = cell.get("row"), cell.get("col")
                row_span, col_span = cell.get("row_span", 1), cell.get("col_span", 1)
                if (not all(type(v) is int for v in (row, col, row_span, col_span)) or
                        row < 0 or col < 0 or row_span < 1 or col_span < 1):
                    details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:invalid_grid")
                    continue
                coords = {(r, c) for r in range(row, row + row_span)
                          for c in range(col, col + col_span)}
                if coords & occupied:
                    details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:overlap")
                    continue
                occupied.update(coords)
                bbox = cell.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:no_bbox")
                    continue
                source_box = pymupdf.Rect(bbox) * page.derotation_matrix
                source_text = page.get_textbox(source_box + (-1, -1, 1, 1))
                if _normalise_text(cell.get("text")) != _normalise_text(source_text):
                    details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:text_diff")
                    continue
                details["matched_cell_count"] += 1
            for field in ("caption",):
                value = _normalise_text(table.get(field))
                if value and value not in page_text:
                    details["mismatches"].append(f"table_{table_index}:{field}_not_in_source_layer")
            for field in ("footnotes", "unit_context"):
                for item_index, item in enumerate(table.get(field) or []):
                    value = _normalise_text(item.get("text") if isinstance(item, dict) else item)
                    if value and value not in page_text:
                        details["mismatches"].append(
                            f"table_{table_index}:{field}_{item_index}_not_in_source_layer")
    passed = details["cell_count"] > 0 and not details["mismatches"]
    return passed, details


def _lines_in_bbox(lines: list[dict[str, Any]], bbox: list[float]) -> list[dict[str, Any]]:
    selected = [line for line in lines
                if line_belongs_to_region([float(v) for v in line["bbox"]], bbox)]
    selected.sort(key=lambda row: (round(float(row["bbox"][1]), 1),
                                   round(float(row["bbox"][0]), 1)))
    return selected


def _verify_ocr_table_region(region: dict[str, Any], lines: list[dict[str, Any]], *,
                             confidence_threshold: float = 0.95) -> tuple[bool, dict[str, Any]]:
    tables = region.get("tables") or []
    details: dict[str, Any] = {"table_count": len(tables), "cell_count": 0,
                               "matched_cell_count": 0, "mismatches": [],
                               "verification_authority": "automatic_local_model_comparison"}
    page_text = strict_text("".join(str(line.get("text") or "") for line in lines))
    if not tables:
        details["mismatches"].append("no_table_result")
        return False, details
    used_line_ids: set[str] = set()
    for table_index, table in enumerate(tables):
        cells = table.get("cells") or []
        if not cells:
            details["mismatches"].append(f"table_{table_index}:no_cells")
            continue
        occupied: set[tuple[int, int]] = set()
        for cell_index, cell in enumerate(cells):
            details["cell_count"] += 1
            bbox = cell.get("bbox")
            row, col = cell.get("row"), cell.get("col")
            row_span, col_span = cell.get("row_span", 1), cell.get("col_span", 1)
            if (not all(type(v) is int for v in (row, col, row_span, col_span)) or
                    row < 0 or col < 0 or row_span < 1 or col_span < 1):
                details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:invalid_grid")
                continue
            coords = {(r, c) for r in range(row, row + row_span)
                      for c in range(col, col + col_span)}
            if coords & occupied:
                details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:overlap")
                continue
            occupied.update(coords)
            if not isinstance(bbox, list) or len(bbox) != 4:
                details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:no_bbox")
                continue
            selected = _lines_in_bbox(lines, [float(v) for v in bbox])
            line_ids = {str(line.get("line_id")) for line in selected}
            if line_ids & used_line_ids:
                details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:ocr_line_reused")
                continue
            expected = strict_text(cell.get("text"))
            actual = strict_text("".join(str(line.get("text") or "") for line in selected))
            confidence_ok = all(float(line.get("score") or 0) >= confidence_threshold
                                for line in selected)
            if expected != actual or (selected and not confidence_ok):
                details["mismatches"].append(f"table_{table_index}:cell_{cell_index}:text_diff")
                continue
            used_line_ids.update(line_ids)
            details["matched_cell_count"] += 1
        for field in ("caption",):
            value = strict_text(table.get(field))
            if value and value not in page_text:
                details["mismatches"].append(f"table_{table_index}:{field}_not_in_independent_ocr")
    passed = details["cell_count"] > 0 and not details["mismatches"]
    return passed, details


def _execution_status(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("execution_status") or value.get("status") or "")
    return ""


def _entry(
    *,
    object_type: str,
    object_id: str,
    source_identity: dict[str, Any],
    execution_status: str,
    conclusion: str,
    methods: Iterable[str],
    input_hashes: dict[str, str],
    evidence_details: dict[str, Any] | None = None,
    uncertainty: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "object_type": object_type,
        "object_id": object_id,
        "source_identity": source_identity,
        "execution_status": execution_status,
        "content_quality_conclusion": conclusion,
        "evidence": {
            "methods": sorted(set(methods)),
            "input_hashes": dict(sorted(input_hashes.items())),
            "details": evidence_details or {},
        },
        "uncertainty": sorted(set(uncertainty or [])),
    }
    result["record_sha256"] = _json_sha(result)
    return result


def _replace_conclusion(
    entry: dict[str, Any],
    conclusion: str,
    *,
    method: str,
    uncertainty: list[str] | None = None,
    child_conclusions: Counter[str] | None = None,
) -> None:
    """Update a roll-up record and keep its content hash trustworthy."""
    entry["content_quality_conclusion"] = conclusion
    entry["evidence"]["methods"] = sorted(set(entry["evidence"]["methods"] + [method]))
    if child_conclusions is not None:
        entry["evidence"]["details"]["child_conclusion_counts"] = dict(
            sorted(child_conclusions.items())
        )
    entry["uncertainty"] = sorted(set(uncertainty or []))
    entry.pop("record_sha256", None)
    entry["record_sha256"] = _json_sha(entry)


def _rollup_conclusion(values: Iterable[str]) -> tuple[str, Counter[str]]:
    counts = Counter(values)
    if counts.get("failed"):
        return "failed", counts
    if counts.get("manual_review_required"):
        return "manual_review_required", counts
    if counts and counts.get("unsupported") == sum(counts.values()):
        return "unsupported", counts
    if counts and counts.get("not_detected") == sum(counts.values()):
        return "not_detected", counts
    return "verified_passed", counts


def _issue(code: str, object_type: str, object_id: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "object_type": object_type, "object_id": object_id, **details}


def _asset_checks(root: Path, assets: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    methods: list[str] = []
    issues: list[dict[str, Any]] = []
    for asset in assets:
        ref = asset.get("ref") or asset.get("image_ref")
        expected = asset.get("sha256") or asset.get("image_sha256")
        if not ref or not expected:
            issues.append({"code": "asset_reference_or_hash_missing", "ref": ref})
            continue
        path = (root / str(ref)).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            issues.append({"code": "asset_missing", "ref": str(ref)})
            continue
        actual = _sha256_file(path)
        if actual != expected:
            issues.append({"code": "asset_hash_mismatch", "ref": str(ref), "actual": actual})
            continue
        methods.append("local_asset_sha256_match")
    return methods, issues


def build_quality_ledger(
    root: Path | str,
    manifest: dict[str, Any],
    page_documents: list[dict[str, Any]],
    canonicals: list[dict[str, Any]],
    *,
    independent_text_verifications: dict[str, dict[str, Any]] | None = None,
    independent_page_evidence: dict[tuple[str, int], dict[str, Any]] | None = None,
    source_signals: list[dict[str, Any]] | None = None,
    artifact_input_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a deterministic ledger without mutating any source artifact."""
    root = Path(root).resolve()
    issues: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    assets = {str(a.get("sha256")): a for a in manifest.get("assets") or []}
    page_docs = {str(d.get("sha256")): d for d in page_documents}
    canonical_docs = {str(d.get("source", {}).get("sha256")): d for d in canonicals}
    pdf_text: dict[tuple[str, int], str] = {}
    page_dims: dict[tuple[str, int], tuple[float, float]] = {}
    pdf_paths: dict[str, Path] = {}
    page_routes: dict[tuple[str, int], str] = {}
    source_crop_hashes: dict[str, str] = {}
    regions: dict[str, dict[str, Any]] = {}
    region_entries: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    element_refs: defaultdict[str, list[str]] = defaultdict(list)
    independent_text_verifications = independent_text_verifications or {}
    independent_page_evidence = independent_page_evidence or {}
    source_signals = source_signals or []
    verified_text_by_page: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    canonical_text_by_page: defaultdict[tuple[str, int], list[str]] = defaultdict(list)

    if len(assets) != len(manifest.get("assets") or []):
        issues.append(_issue("duplicate_manifest_asset_sha", "corpus", str(manifest.get("corpus_version"))))

    pi_hash = _json_sha(page_documents)
    canonical_hash = _json_sha(canonicals)
    manifest_hash = _json_sha(manifest)

    for sha, asset in sorted(assets.items()):
        rel = str(asset.get("canonical_rel_path") or "")
        pdf_path = (root / rel).resolve()
        methods = ["manifest_source_identity"]
        document_issues: list[str] = []
        actual_sha = None
        page_count = None
        if not pdf_path.is_relative_to(root) or not pdf_path.is_file():
            issues.append(_issue("source_pdf_missing", "source_document", sha, path=rel))
            document_issues.append("source_pdf_missing")
        else:
            pdf_paths[sha] = pdf_path
            actual_sha = _sha256_file(pdf_path)
            if actual_sha == sha:
                methods.append("source_pdf_sha256_match")
            else:
                issues.append(_issue("source_pdf_hash_mismatch", "source_document", sha, actual=actual_sha))
                document_issues.append("source_pdf_hash_mismatch")
            with pymupdf.open(pdf_path) as pdf:
                page_count = len(pdf)
                if page_count == asset.get("page_count"):
                    methods.append("source_pdf_page_count_match")
                else:
                    issues.append(_issue("source_pdf_page_count_mismatch", "source_document", sha,
                                         actual=page_count, expected=asset.get("page_count")))
                    document_issues.append("source_pdf_page_count_mismatch")
                for number, page in enumerate(pdf, 1):
                    pdf_text[(sha, number)] = _normalise_text(page.get_text("text"))
                    page_dims[(sha, number)] = (float(page.rect.width), float(page.rect.height))
        entries.append(_entry(
            object_type="source_document", object_id=sha,
            source_identity={"file_sha256": sha, "path": rel},
            execution_status="completed" if not document_issues else "failed",
            conclusion="manual_review_required" if not document_issues else "failed",
            methods=methods,
            input_hashes={"manifest": manifest_hash, "source_pdf": actual_sha or "missing"},
            uncertainty=[],
        ))

    for sha, doc in sorted(page_docs.items()):
        if sha not in assets:
            issues.append(_issue("page_document_not_in_manifest", "source_document", sha))
        canonical = canonical_docs.get(sha)
        if canonical is None:
            issues.append(_issue("canonical_document_missing", "source_document", sha))
        canonical_pages = {p.get("physical_page"): p for p in (canonical or {}).get("pages") or []}
        seen_pages: set[int] = set()
        for page in doc.get("pages") or []:
            number = int(page.get("physical_page") or 0)
            page_id = f"{sha}:p{number:04d}"
            if number in seen_pages:
                issues.append(_issue("duplicate_physical_page", "source_page", page_id))
            seen_pages.add(number)
            width, height = page_dims.get((sha, number), (float(page.get("width") or 0), float(page.get("height") or 0)))
            page_routes[(sha, number)] = str(page.get("extraction_route") or "")
            page_methods = ["page_intermediate_record_present"]
            if number in canonical_pages:
                page_methods.append("canonical_page_record_present")
            else:
                issues.append(_issue("canonical_page_missing", "source_page", page_id))
            layout_status = str(page.get("layout_status") or "")
            conclusion = "manual_review_required" if layout_status == "completed" else "failed"
            if layout_status != "completed":
                issues.append(_issue("page_layout_not_completed", "source_page", page_id, status=layout_status))
            entries.append(_entry(
                object_type="source_page", object_id=page_id,
                source_identity={"file_sha256": sha, "physical_page": number},
                execution_status="completed" if layout_status == "completed" else "failed",
                conclusion=conclusion, methods=page_methods,
                input_hashes={"page_intermediate": pi_hash, "source_pdf": sha},
                uncertainty=["layout_recall_requires_independent_content_checks"],
            ))
            summaries = page.get("content_types") or {}
            if set(summaries) != set(CONTENT_TYPES):
                issues.append(_issue("four_type_summary_missing", "source_page", page_id,
                                     present=sorted(summaries)))
            for kind in CONTENT_TYPES:
                status = _execution_status(summaries.get(kind))
                type_id = f"{page_id}:{kind}"
                if status not in TERMINAL_EXECUTION:
                    issues.append(_issue("page_type_nonterminal_or_missing", "page_content_type", type_id,
                                         status=status))
                    conclusion = "failed"
                elif status == "failed":
                    conclusion = "failed"
                elif status == "unsupported":
                    conclusion = "unsupported"
                elif status == "not_detected":
                    conclusion = "not_detected"
                else:
                    conclusion = "manual_review_required"
                entries.append(_entry(
                    object_type="page_content_type", object_id=type_id,
                    source_identity={"file_sha256": sha, "physical_page": number, "kind": kind},
                    execution_status=status or "missing", conclusion=conclusion,
                    methods=["page_four_type_status"],
                    input_hashes={"page_intermediate": pi_hash},
                    uncertainty=[] if status == "not_detected" else ["type_status_is_execution_not_content_approval"],
                ))
            for region in page.get("regions") or []:
                rid = str(region.get("region_id") or "")
                if not rid:
                    issues.append(_issue("source_region_id_missing", "source_region", page_id))
                    continue
                if rid in regions:
                    issues.append(_issue("duplicate_source_region_id", "source_region", rid))
                    continue
                regions[rid] = region
                if region.get("file_sha256") != sha or region.get("physical_page") != number:
                    issues.append(_issue("source_region_identity_mismatch", "source_region", rid))
                if not _bbox_valid(region.get("bbox"), width, height):
                    issues.append(_issue("source_region_bbox_invalid", "source_region", rid,
                                         bbox=region.get("bbox")))

            if pdf_paths.get(sha):
                source_crop_hashes.update(original_page_crop_hashes(
                    pdf_paths[sha], number, page.get("regions") or []))

    for sha, canonical in sorted(canonical_docs.items()):
        for outcome in canonical.get("structured_outcomes") or []:
            rid = str(outcome.get("region_id") or "")
            if not rid:
                issues.append(_issue("structured_outcome_region_id_missing", "source_document", sha))
            elif rid in outcomes:
                issues.append(_issue("duplicate_structured_outcome", "source_region", rid))
            else:
                outcomes[rid] = outcome
        for element in canonical.get("elements") or []:
            eid = str(element.get("element_id") or "")
            for rid in element.get("source_region_ids") or []:
                element_refs[str(rid)].append(eid)

    for rid, region in sorted(regions.items()):
        sha = str(region.get("file_sha256") or "")
        number = int(region.get("physical_page") or 0)
        kind = str(region.get("kind") or "")
        status = str(region.get("execution_status") or "")
        methods = ["page_intermediate_region_record"]
        uncertainty = list(region.get("issues") or [])
        conclusion = "manual_review_required"
        table_details: dict[str, Any] | None = None
        if status == "failed":
            conclusion = "failed"
        elif status == "unsupported":
            conclusion = "unsupported"
        elif kind == "text":
            source_text = _normalise_text(region.get("text"))
            layer = pdf_text.get((sha, number), "")
            route = page_routes.get((sha, number), "")
            if route == "native" and source_text and source_text in layer and region.get("quality_status") == "passed":
                conclusion = "verified_passed"
                methods.append("independent_pdf_text_layer_match")
                uncertainty = []
            else:
                uncertainty.append("ocr_or_text_layer_match_not_sufficient_for_automatic_approval")
            independent = independent_text_verifications.get(rid)
            if independent:
                expected_hash = _sha256_bytes(source_text.encode("utf-8"))
                identity_matches = (
                    independent.get("file_sha256") == sha and
                    independent.get("physical_page") == number and
                    independent.get("source_text_sha256") == expected_hash
                )
                if (identity_matches and
                        independent.get("automatic_conclusion") == "verified_passed" and
                        independent.get("verification_authority") == "automatic_local_model_comparison" and
                        independent.get("strict_text_match") is True):
                    conclusion = "verified_passed"
                    methods.append(str(independent.get("method") or "independent_local_ocr_match"))
                    uncertainty = []
                elif not identity_matches:
                    issues.append(_issue("independent_text_evidence_identity_mismatch",
                                         "source_region", rid))
        else:
            outcome = outcomes.get(rid)
            if outcome is None:
                issues.append(_issue("structured_region_without_disposition", "source_region", rid))
            else:
                methods.append("canonical_structured_outcome")
                out_status = str(outcome.get("execution_status") or "")
                if out_status == "unsupported" or (outcome.get("element_id") is None and status == "unsupported"):
                    conclusion = "unsupported"
                elif out_status == "failed":
                    conclusion = "failed"
                elif outcome.get("element_id"):
                    conclusion = "manual_review_required"
                else:
                    issues.append(_issue("structured_outcome_has_no_terminal_disposition", "source_region", rid))
            expected_crop = source_crop_hashes.get(rid)
            if expected_crop and expected_crop == region.get("image_sha256"):
                methods.append("source_pdf_crop_sha256_match")
                if kind == "image" and outcome and outcome.get("element_id"):
                    conclusion = "verified_passed"
                    uncertainty = []
            elif region.get("image_sha256"):
                issues.append(_issue("region_image_differs_from_original_pdf_crop",
                                     "source_region", rid))
                conclusion = "failed"
            if (kind == "table" and conclusion != "failed" and
                    page_routes.get((sha, number)) == "native" and pdf_paths.get(sha)):
                table_passed, table_details = _verify_native_table_region(
                    pdf_paths[sha], number, region)
                methods.append("native_pdf_full_cell_text_and_grid_check")
                if table_passed and outcome and outcome.get("element_id"):
                    conclusion = "verified_passed"
                    uncertainty = []
                else:
                    uncertainty.append("table_full_cell_or_structure_check_not_passed")
            elif kind == "table" and conclusion != "failed" and (sha, number) in independent_page_evidence:
                table_passed, table_details = _verify_ocr_table_region(
                    region, independent_page_evidence[(sha, number)].get("lines") or [])
                methods.append("independent_rapidocr_ppocrv6_full_cell_text_and_grid_check")
                if table_passed and outcome and outcome.get("element_id"):
                    conclusion = "verified_passed"
                    uncertainty = []
                else:
                    uncertainty.append("table_full_cell_or_structure_check_not_passed")
        region_assets = []
        if region.get("image_ref") or region.get("image_sha256"):
            region_assets.append({"ref": region.get("image_ref"), "sha256": region.get("image_sha256")})
        asset_methods, asset_issues = _asset_checks(root, region_assets)
        methods.extend(asset_methods)
        for item in asset_issues:
            issues.append(_issue(item.pop("code"), "source_region", rid, **item))
        entry = _entry(
            object_type="source_region", object_id=rid,
            source_identity={
                "file_sha256": sha, "physical_page": number,
                "bbox": region.get("bbox"), "region_id": rid, "kind": kind,
                "asset_ref": region.get("image_ref"),
            },
            execution_status=status, conclusion=conclusion, methods=methods,
            input_hashes={"page_intermediate": pi_hash, "source_pdf": sha,
                          **({"asset": region.get("image_sha256")} if region.get("image_sha256") else {})},
            evidence_details={"canonical_element_ids": sorted(element_refs.get(rid, [])),
                              "structured_outcome": bool(outcomes.get(rid)),
                              **({"native_table_check": table_details}
                                 if table_details is not None else {}),
                              **({"independent_text_verification": independent}
                                 if kind == "text" and independent else {})},
            uncertainty=uncertainty,
        )
        entries.append(entry)
        region_entries[rid] = entry

    for sha, canonical in sorted(canonical_docs.items()):
        seen_elements: set[str] = set()
        pages = {p.get("physical_page"): p for p in canonical.get("pages") or []}
        for element in canonical.get("elements") or []:
            eid = str(element.get("element_id") or "")
            if not eid:
                issues.append(_issue("canonical_element_id_missing", "canonical_element", sha))
                continue
            if eid in seen_elements:
                issues.append(_issue("duplicate_canonical_element_id", "canonical_element", eid))
            seen_elements.add(eid)
            refs = [str(r) for r in element.get("source_region_ids") or []]
            missing_refs = [r for r in refs if r not in regions]
            for rid in missing_refs:
                issues.append(_issue("canonical_source_region_missing", "canonical_element", eid,
                                     region_id=rid))
            spans = element.get("source_spans") or []
            if not spans:
                issues.append(_issue("canonical_source_span_missing", "canonical_element", eid))
            for span in spans:
                pno = span.get("physical_page")
                page = pages.get(pno) or {}
                if not _bbox_valid(span.get("bbox"), float(page.get("width") or 0), float(page.get("height") or 0)):
                    issues.append(_issue("canonical_source_span_invalid", "canonical_element", eid,
                                         span_id=span.get("span_id")))
            methods = ["canonical_element_record", "canonical_source_span_bounds"]
            conclusion = "manual_review_required"
            if refs and all(region_entries.get(r, {}).get("content_quality_conclusion") == "verified_passed" for r in refs):
                if element.get("type") == "text":
                    element_text = _normalise_text(_content_text(element))
                    source_texts = [_normalise_text(regions[r].get("text")) for r in refs]
                    joined_source = "".join(text for text in source_texts if text)
                    if (element_text and (element_text == joined_source or any(
                            element_text in text or text in element_text
                            for text in source_texts if text))):
                        conclusion = "verified_passed"
                        methods.append("canonical_text_matches_source_region")
                elif element.get("type") == "table" and len(refs) == 1:
                    source_tables = regions[refs[0]].get("tables") or []
                    canonical_signature = _table_cells_signature(
                        (element.get("content") or {}).get("cells") or [], canonical=True)
                    if canonical_signature and any(
                            canonical_signature == _table_cells_signature(table.get("cells") or [])
                            for table in source_tables):
                        conclusion = "verified_passed"
                        methods.append("canonical_table_matches_verified_source_table")
                elif element.get("type") == "figure":
                    content = element.get("content") or {}
                    source_pages = {int(span.get("physical_page") or 0)
                                    for span in spans}
                    text_values = [content.get("caption"),
                                   *(ref.get("text") for ref in content.get("references") or [])]
                    normalized_values = [_normalise_text(value) for value in text_values if _normalise_text(value)]
                    if normalized_values and all(any(
                            value in pdf_text.get((sha, page_no), "") for page_no in source_pages)
                            for value in normalized_values):
                        conclusion = "verified_passed"
                        methods.append("canonical_figure_caption_reference_pdf_layer_match")
                    elif normalized_values and all(any(
                            value in strict_text("".join(
                                str(line.get("text") or "")
                                for line in independent_page_evidence.get((sha, page_no), {}).get("lines") or []))
                            for page_no in source_pages)
                            for value in normalized_values):
                        conclusion = "verified_passed"
                        methods.append("canonical_figure_caption_reference_independent_ocr_match")
            if conclusion != "verified_passed" and element.get("type") == "text":
                element_text = strict_text(_content_text(element))
                selected_lines: list[tuple[int, float, float, dict[str, Any]]] = []
                seen_lines: set[tuple[int, str]] = set()
                for span in spans:
                    page_no = int(span.get("physical_page") or 0)
                    evidence = independent_page_evidence.get((sha, page_no))
                    bbox = span.get("bbox")
                    if not evidence or not isinstance(bbox, list) or len(bbox) != 4:
                        continue
                    for line in _lines_in_bbox(evidence.get("lines") or [], [float(v) for v in bbox]):
                        key = (page_no, str(line.get("line_id")))
                        if key in seen_lines:
                            continue
                        seen_lines.add(key)
                        selected_lines.append((page_no, float(line["bbox"][1]),
                                               float(line["bbox"][0]), line))
                selected_lines.sort(key=lambda item: item[:3])
                independent_text = strict_text("".join(str(item[3].get("text") or "")
                                                       for item in selected_lines))
                confidence_ok = bool(selected_lines) and all(
                    float(item[3].get("score") or 0) >= 0.95 for item in selected_lines)
                if element_text and element_text == independent_text and confidence_ok:
                    conclusion = "verified_passed"
                    methods.append("canonical_text_independent_ocr_span_match")
            if element.get("execution_status") == "failed":
                conclusion = "failed"
            asset_rows = list(element.get("crop_assets") or [])
            content_asset = (element.get("content") or {}).get("asset")
            if isinstance(content_asset, dict):
                asset_rows.append(content_asset)
            asset_methods, asset_issues = _asset_checks(root, asset_rows)
            methods.extend(asset_methods)
            for item in asset_issues:
                issues.append(_issue(item.pop("code"), "canonical_element", eid, **item))
            if element.get("type") == "text":
                for page_no in sorted({int(span.get("physical_page") or 0) for span in spans}):
                    canonical_text_by_page[(sha, page_no)].append(_normalise_text(_content_text(element)))
                    if conclusion == "verified_passed":
                        verified_text_by_page[(sha, page_no)].append(
                            _normalise_text(_content_text(element)))
            entries.append(_entry(
                object_type="canonical_element", object_id=f"{sha}:{eid}",
                source_identity={
                    "file_sha256": sha,
                    "element_id": eid,
                    "element_type": element.get("type"),
                    "region_ids": refs,
                    "source_spans": spans,
                    "asset_refs": sorted({str(a.get("ref")) for a in asset_rows if a.get("ref")}),
                },
                execution_status=str(element.get("execution_status") or "completed"),
                conclusion=conclusion, methods=methods,
                input_hashes={"canonical": canonical_hash, "source_pdf": sha},
                evidence_details={"quality_status": element.get("quality_status"),
                                  "quality_issues": element.get("quality_issues") or []},
                uncertainty=([] if conclusion == "verified_passed" else
                             list(element.get("quality_issues") or []) or
                             ["content_not_independently_verified"]),
            ))

    embedded_signal_info: dict[tuple[str, int, int], dict[str, Any]] = {}
    embedded_digest_counts: Counter[str] = Counter()
    embedded_page_area_sums: defaultdict[tuple[str, int], float] = defaultdict(float)
    repeated_text_pages: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    vector_signals_by_page: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    vector_coincident_text: dict[tuple[str, int, int], str] = {}
    for signal in source_signals:
        sha = str(signal.get("file_sha256") or "")
        number = int(signal.get("physical_page") or 0)
        if signal.get("reason") == "original_text_block_not_covered_by_layout":
            repeated_text_pages[(sha, _normalise_text(signal.get("original_text")))].add(number)
        if signal.get("reason") == "original_vector_graphic_candidate_not_assigned":
            vector_signals_by_page[(sha, number)].append(signal)
        if signal.get("reason") != "original_embedded_image_candidate_not_assigned":
            continue
        index = int(signal.get("source_image_index") or 0)
        pdf_path = pdf_paths.get(sha)
        if not pdf_path:
            continue
        with pymupdf.open(pdf_path) as pdf:
            page = pdf[number - 1]
            infos = page.get_image_info(hashes=True)
            if index >= len(infos):
                continue
            info = infos[index]
            digest = info.get("digest")
            digest_hex = digest.hex() if isinstance(digest, bytes) else str(digest or "")
            bbox = list(pymupdf.Rect(info["bbox"]) * page.rotation_matrix)
            row = {"digest": digest_hex, "bbox": bbox,
                   "page_area": float(page.rect.get_area())}
            embedded_signal_info[(sha, number, index)] = row
            embedded_page_area_sums[(sha, number)] += pymupdf.Rect(bbox).get_area()
            if digest_hex:
                embedded_digest_counts[digest_hex] += 1

    for (sha, number), signals_on_page in vector_signals_by_page.items():
        pdf_path = pdf_paths.get(sha)
        if not pdf_path:
            continue
        with pymupdf.open(pdf_path) as pdf:
            page = pdf[number - 1]
            for signal in signals_on_page:
                index = int(signal.get("source_drawing_index") or 0)
                bbox = signal.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                source_box = pymupdf.Rect(bbox) * page.derotation_matrix
                coincident = _normalise_text(page.get_textbox(source_box + (-1, -1, 1, 1)))
                if coincident:
                    vector_coincident_text[(sha, number, index)] = coincident

    for signal in source_signals:
        sha = str(signal.get("file_sha256") or "")
        number = int(signal.get("physical_page") or 0)
        reason = str(signal.get("reason") or "unknown")
        bbox = signal.get("bbox")
        source_index = (signal.get("source_block_index") if signal.get("source_block_index") is not None
                        else signal.get("source_image_index") if signal.get("source_image_index") is not None
                        else signal.get("source_drawing_index"))
        identity_payload = {"file_sha256": sha, "physical_page": number, "reason": reason,
                            "bbox": bbox, "source_index": source_index}
        signal_id = "sig-" + _json_sha(identity_payload)[:24]
        conclusion = "manual_review_required"
        methods = ["independent_original_pdf_source_signal"]
        uncertainty = [reason]
        details: dict[str, Any] = {"reason": reason}
        original_text = str(signal.get("original_text") or "")
        if original_text:
            details["original_text"] = original_text
            details["original_text_sha256"] = _sha256_bytes(
                _normalise_text(original_text).encode("utf-8"))
        if reason == "original_text_block_not_covered_by_layout":
            text_value = _normalise_text(original_text)
            canonical_page_text = "".join(canonical_text_by_page.get((sha, number), []))
            if text_value and text_value in canonical_page_text:
                conclusion = "verified_passed"
                methods.append("source_text_signal_exactly_preserved_in_canonical_text")
                uncertainty = []
            elif text_value and len(repeated_text_pages[(sha, text_value)]) >= 3 and isinstance(bbox, list):
                width, height = page_dims.get((sha, number), (0.0, 0.0))
                rect = pymupdf.Rect(bbox)
                page_area = width * height
                outside = rect.x0 < 0 or rect.y0 < 0 or rect.x1 > width or rect.y1 > height
                if page_area and (rect.get_area() >= 0.20 * page_area or outside):
                    conclusion = "verified_passed"
                    methods.append("repeated_large_text_overlay_classified_as_watermark")
                    uncertainty = []
                    details["disposition"] = "repeated_non_body_watermark_not_canonical_content"
                    details["same_text_page_count"] = len(repeated_text_pages[(sha, text_value)])
            elif (text_value and not any(character.isalnum() for character in text_value) and
                  isinstance(bbox, list) and len(bbox) == 4):
                signal_rect = pymupdf.Rect(bbox)
                matching_lines = []
                for line in independent_page_evidence.get((sha, number), {}).get("lines") or []:
                    line_bbox = line.get("bbox")
                    line_text = _normalise_text(line.get("text"))
                    if (not isinstance(line_bbox, list) or len(line_bbox) != 4 or
                            not line_text or line_text not in canonical_page_text or
                            float(line.get("score") or 0) < 0.95):
                        continue
                    line_rect = pymupdf.Rect(line_bbox)
                    overlap = (signal_rect & line_rect).get_area()
                    denominator = min(signal_rect.get_area(), line_rect.get_area())
                    if denominator and overlap >= 0.50 * denominator:
                        matching_lines.append(line)
                if matching_lines:
                    conclusion = "verified_passed"
                    methods.append("corrupt_native_text_layer_token_replaced_by_verified_ocr")
                    uncertainty = []
                    details["disposition"] = (
                        "corrupt_native_text_layer_token_replaced_by_verified_ocr")
                    details["independent_ocr_lines"] = [
                        {"bbox": line.get("bbox"), "text": line.get("text"),
                         "score": line.get("score")}
                        for line in matching_lines
                    ]
        elif reason in {
            "original_table_caption_candidate_not_assigned",
            "original_figure_caption_candidate_not_assigned",
        }:
            text_value = _normalise_text(original_text)
            canonical_page_text = "".join(canonical_text_by_page.get((sha, number), []))
            if text_value and text_value in canonical_page_text:
                conclusion = "verified_passed"
                methods.append("source_caption_signal_exactly_preserved_in_canonical_text")
                uncertainty = []
        elif reason == "original_vector_graphic_candidate_not_assigned" and isinstance(bbox, list):
            signal_rect = pymupdf.Rect(bbox)
            matches = []
            for rid, region in regions.items():
                if (region.get("file_sha256") != sha or region.get("physical_page") != number or
                        not region.get("bbox") or
                        region_entries.get(rid, {}).get("content_quality_conclusion") != "verified_passed"):
                    continue
                overlap = (signal_rect & pymupdf.Rect(region["bbox"])).get_area()
                if signal_rect.get_area() and overlap >= 0.90 * signal_rect.get_area():
                    matches.append(rid)
            if matches:
                conclusion = "verified_passed"
                methods.append("vector_signal_contained_in_verified_source_region")
                uncertainty = []
                details["verified_source_region_ids"] = sorted(matches)
            else:
                coincident_text = vector_coincident_text.get(
                    (sha, number, int(signal.get("source_drawing_index") or 0)), "")
                if coincident_text:
                    conclusion = "verified_passed"
                    methods.append("vector_signal_coincident_with_native_text_layer")
                    uncertainty = []
                    details["coincident_text_sha256"] = _sha256_bytes(coincident_text.encode("utf-8"))
                else:
                    canonical_page_text = "".join(canonical_text_by_page.get((sha, number), []))
                    matching_lines = []
                    for line in independent_page_evidence.get((sha, number), {}).get("lines") or []:
                        line_bbox = line.get("bbox")
                        line_text = _normalise_text(line.get("text"))
                        if (not isinstance(line_bbox, list) or len(line_bbox) != 4 or
                                not line_text or line_text not in canonical_page_text or
                                float(line.get("score") or 0) < 0.95):
                            continue
                        overlap = (signal_rect & pymupdf.Rect(line_bbox)).get_area()
                        if signal_rect.get_area() and overlap >= 0.80 * signal_rect.get_area():
                            matching_lines.append(line)
                    if matching_lines:
                        conclusion = "verified_passed"
                        methods.append("vector_signal_is_text_outline_preserved_by_independent_ocr")
                        uncertainty = []
                        details["disposition"] = "vector_text_outline_not_separate_semantic_graphic"
                        details["independent_ocr_lines"] = [
                            {"bbox": line.get("bbox"), "text": line.get("text"),
                             "score": line.get("score")}
                            for line in matching_lines
                        ]
        elif reason == "original_full_page_image_layout_semantics_not_independently_verified":
            page_evidence = independent_page_evidence.get((sha, number))
            if (page_evidence and page_evidence.get("file_sha256", sha) == sha and
                    int(page_evidence.get("physical_page", number)) == number and
                    page_evidence.get("lines")):
                conclusion = "verified_passed"
                methods.append("full_page_scan_classified_as_source_carrier_with_independent_ocr")
                uncertainty = []
                details["disposition"] = "source_page_carrier_not_semantic_figure"
                details["page_extraction_route"] = page_routes.get((sha, number))
        elif reason == "original_embedded_image_candidate_not_assigned":
            info = embedded_signal_info.get((sha, number, int(source_index or 0)))
            if info:
                image_area = pymupdf.Rect(info["bbox"]).get_area()
                repeated = embedded_digest_counts.get(info["digest"], 0)
                details.update({"embedded_image_digest": info["digest"],
                                "same_digest_signal_count": repeated,
                                "page_area_ratio": image_area / info["page_area"] if info["page_area"] else None})
                if (info["digest"] and repeated >= 3 and info["page_area"] and
                        image_area <= 0.05 * info["page_area"]):
                    conclusion = "verified_passed"
                    methods.append("repeated_small_embedded_image_classified_as_decoration")
                    uncertainty = []
                    details["disposition"] = "repeated_decorative_asset_not_semantic_figure"
                elif (info["page_area"] and
                      embedded_page_area_sums[(sha, number)] >= 0.80 * info["page_area"] and
                      (sha, number) in independent_page_evidence):
                    conclusion = "verified_passed"
                    methods.append("tiled_page_scan_classified_as_source_carrier_with_independent_ocr")
                    uncertainty = []
                    details["disposition"] = "tiled_source_page_carrier_not_semantic_figure"
                    details["page_tile_coverage_ratio"] = (
                        embedded_page_area_sums[(sha, number)] / info["page_area"])
        entries.append(_entry(
            object_type="source_signal", object_id=signal_id,
            source_identity={**identity_payload, "signal_id": signal_id},
            execution_status="completed", conclusion=conclusion,
            methods=methods,
            input_hashes={"source_pdf": sha, **({"structure_audit": artifact_input_hashes.get("structure_audit_file", "")}
                                                if artifact_input_hashes else {})},
            evidence_details=details, uncertainty=uncertainty,
        ))

    # Parent records are quality roll-ups, not synonyms for successful
    # execution.  Derive them only after every unique source object and every
    # Canonical projection has its own conclusion.
    type_entries: dict[tuple[str, int, str], dict[str, Any]] = {}
    page_entries: dict[tuple[str, int], dict[str, Any]] = {}
    document_entries: dict[str, dict[str, Any]] = {}
    type_children: defaultdict[tuple[str, int, str], list[str]] = defaultdict(list)
    page_signal_children: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    for entry in entries:
        identity = entry["source_identity"]
        object_type = entry["object_type"]
        if object_type == "source_document":
            document_entries[str(identity["file_sha256"])] = entry
        elif object_type == "source_page":
            page_entries[(str(identity["file_sha256"]), int(identity["physical_page"]))] = entry
        elif object_type == "page_content_type":
            type_entries[(str(identity["file_sha256"]), int(identity["physical_page"]),
                          str(identity["kind"]))] = entry
        elif object_type == "source_region":
            key = (str(identity["file_sha256"]), int(identity["physical_page"]),
                   str(identity["kind"]))
            type_children[key].append(str(entry["content_quality_conclusion"]))
        elif object_type == "canonical_element":
            kind = "image" if identity.get("element_type") == "figure" else str(identity.get("element_type"))
            for page_no in sorted({int(s["physical_page"]) for s in identity.get("source_spans") or []}):
                key = (str(identity["file_sha256"]), page_no, kind)
                type_children[key].append(str(entry["content_quality_conclusion"]))
        elif object_type == "source_signal":
            page_signal_children[(str(identity["file_sha256"]),
                                  int(identity["physical_page"]))].append(
                                      str(entry["content_quality_conclusion"]))

    for key, entry in type_entries.items():
        if entry["execution_status"] != "completed":
            continue
        children = type_children.get(key, [])
        if children:
            conclusion, child_counts = _rollup_conclusion(children)
            _replace_conclusion(entry, conclusion, method="child_object_quality_rollup",
                                uncertainty=[] if conclusion == "verified_passed" else
                                ["one_or_more_child_objects_not_verified"],
                                child_conclusions=child_counts)
        else:
            _replace_conclusion(entry, "manual_review_required",
                                method="child_object_quality_rollup",
                                uncertainty=["completed_type_has_no_verifiable_child_object"])

    for key, entry in page_entries.items():
        if entry["execution_status"] == "failed":
            continue
        children = [type_entries[(key[0], key[1], kind)]["content_quality_conclusion"]
                    for kind in CONTENT_TYPES if (key[0], key[1], kind) in type_entries]
        children.extend(page_signal_children.get(key, []))
        conclusion, child_counts = _rollup_conclusion(children)
        _replace_conclusion(entry, conclusion, method="four_type_quality_rollup",
                            uncertainty=[] if conclusion == "verified_passed" else
                            ["one_or_more_content_types_not_verified"],
                            child_conclusions=child_counts)

    for sha, entry in document_entries.items():
        if entry["execution_status"] == "failed":
            continue
        children = [page["content_quality_conclusion"] for key, page in page_entries.items()
                    if key[0] == sha]
        conclusion, child_counts = _rollup_conclusion(children)
        _replace_conclusion(entry, conclusion, method="page_quality_rollup",
                            uncertainty=[] if conclusion == "verified_passed" else
                            ["one_or_more_pages_not_verified"],
                            child_conclusions=child_counts)

    for rid, region in sorted(regions.items()):
        if region.get("kind") != "text" and rid not in outcomes:
            # Already emitted above; retaining this pass documents the closed
            # set invariant without treating text aggregation as 1:1.
            pass
    for rid in sorted(element_refs):
        if rid not in regions:
            # One issue per orphan identity is sufficient even when several
            # elements cite it.
            if not any(i["code"] == "canonical_source_region_missing" and
                       i.get("region_id") == rid for i in issues):
                issues.append(_issue("canonical_source_region_missing", "source_region", rid,
                                     region_id=rid))
    for sha in sorted(assets):
        if sha not in page_docs:
            issues.append(_issue("page_document_missing", "source_document", sha))
        if sha not in canonical_docs:
            issues.append(_issue("canonical_document_missing", "source_document", sha))

    entries.sort(key=lambda e: (OBJECT_TYPES.index(e["object_type"]), e["object_id"]))
    counts = Counter(e["object_type"] for e in entries)
    conclusions = Counter(e["content_quality_conclusion"] for e in entries)
    execution = Counter(e["execution_status"] for e in entries)
    manual_count = conclusions.get("manual_review_required", 0)
    failed_count = conclusions.get("failed", 0)
    if issues or failed_count:
        gate_status = "failed"
    elif manual_count:
        gate_status = "needs_adjudication"
    else:
        gate_status = "passed"
    summary = {
        "object_counts": {k: counts.get(k, 0) for k in OBJECT_TYPES},
        "conclusion_counts": dict(sorted(conclusions.items())),
        "execution_status_counts": dict(sorted(execution.items())),
        "integrity_issue_count": len(issues),
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": manifest.get("corpus_version"),
        "input_hashes": {
            "manifest": manifest_hash,
            "page_intermediate_aggregate": pi_hash,
            "canonical_aggregate": canonical_hash,
            **dict(sorted((artifact_input_hashes or {}).items())),
        },
        "entries": entries,
        "integrity_issues": sorted(issues, key=lambda i: (i["code"], i["object_type"], i["object_id"])),
        "summary": summary,
        "gate": {
            "status": gate_status,
            "quality_passed": gate_status == "passed",
            "manual_adjudication_count": manual_count,
            "failed_count": failed_count,
        },
    }
    report["ledger_sha256"] = _json_sha({"entries": entries, "integrity_issues": report["integrity_issues"]})
    return report


def write_immutable_quality_report(root: Path | str, report: dict[str, Any]) -> Path:
    """Write a content-addressed report directory without overwriting bytes."""
    root = Path(root).resolve()
    version = str(report.get("corpus_version") or "unknown")
    digest = str(report.get("ledger_sha256") or _json_sha(report))
    target = root / "data" / "quality" / version / f"qa-{digest[:16]}"
    payloads = {
        "quality_ledger.jsonl": b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for row in report.get("entries") or []
        ),
        "quality_summary.json": (json.dumps({
            k: report[k] for k in (
                "schema_version", "corpus_version", "input_hashes", "summary", "gate",
                "ledger_sha256",
            )
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "quality_issues.json": (json.dumps(report.get("integrity_issues") or [],
                                           ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    target.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        path = target / name
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"immutable quality artifact conflict: {path}")
            continue
        fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=target)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return target
