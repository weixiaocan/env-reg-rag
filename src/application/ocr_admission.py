"""Document-level OCR text admission (plan C).

OCR-routed text is assembled as ``needs_review`` (``ocr_source_review``) and
therefore projects to ``source_locator_only``.  A reviewer may admit a whole
document after sampling its chunks against the rendered source pages; the
verdict lives in an explicit, versioned registry file, never in code.

Admission upgrades only text elements whose *sole* blockers are the OCR-source
reasons below.  Anything else (TOC, page furniture, too-short text, region
quality issues, tables/formulas/figures) keeps its original quality.  Every
upgraded element carries ``review_assertion:<admission id>:<sha12>`` in
``quality_issues`` so the verdict stays traceable, mirroring the formula
``review_assertion`` convention.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ADMITTABLE_REASONS = frozenset({
    "ocr_source_review",
    "preprocessed_independent_ocr_unreviewed",
    "quarantined_source",
    "scanned_with_text_layer_requires_ocr_vote",
})
ADMITTED_REASON = "ocr_document_admitted"
VERDICTS = frozenset({"admit", "locator_only"})
# Tables are admitted one by one (keyed by their layout region id), each with
# its own check evidence; the document-level text verdict never covers them.
TABLE_ADMITTABLE_REASONS = ADMITTABLE_REASONS | {"structured_default_locator_only"}
TABLE_ADMITTED_REASON = "table_admitted"
TABLE_METHODS = frozenset({"text_layer_auto_check"})


def load_admission_registry(path: str | Path) -> dict[str, Any]:
    """Load and validate the admission registry; return it with its sha256."""
    raw = Path(path).read_bytes()
    registry = json.loads(raw.decode("utf-8"))
    admission_id = str(registry.get("admission_id") or "").strip()
    if not admission_id:
        raise ValueError("admission registry needs a non-empty admission_id")
    documents = registry.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("admission registry needs a non-empty documents list")
    seen: set[str] = set()
    for record in documents:
        sha = str(record.get("sha256") or "")
        if len(sha) != 64:
            raise ValueError(f"admission record has an invalid sha256: {sha!r}")
        if sha in seen:
            raise ValueError(f"duplicate admission record for {sha[:12]}")
        seen.add(sha)
        if record.get("verdict") not in VERDICTS:
            raise ValueError(f"{sha[:12]}: verdict must be one of {sorted(VERDICTS)}")
        if not record.get("reviewer") or not record.get("samples"):
            raise ValueError(f"{sha[:12]}: reviewer and samples are required evidence")
    regions: set[tuple[str, str]] = set()
    for table in registry.get("table_admissions") or []:
        key = (str(table.get("sha256") or ""), str(table.get("region_id") or ""))
        if len(key[0]) != 64 or not key[1]:
            raise ValueError(f"table admission needs sha256 and region_id: {key!r}")
        if key in regions:
            raise ValueError(f"duplicate table admission for {key[1]}")
        regions.add(key)
        if table.get("method") not in TABLE_METHODS or not table.get("evidence"):
            raise ValueError(f"{key[1]}: table admission needs a known method and evidence")
    return {**registry, "registry_sha256": hashlib.sha256(raw).hexdigest()}


def admission_for(registry: dict[str, Any] | None, sha256: str) -> dict[str, Any] | None:
    if not registry:
        return None
    return next((record for record in registry["documents"]
                 if record["sha256"] == sha256 and record["verdict"] == "admit"), None)


def apply_table_admission(document: dict[str, Any],
                          registry: dict[str, Any] | None) -> int:
    """Upgrade individually checked tables in place; return the count."""
    if not registry:
        return 0
    sha = str((document.get("source") or {}).get("sha256") or "")
    admitted = {table["region_id"] for table in registry.get("table_admissions") or []
                if table["sha256"] == sha}
    upgraded = 0
    for element in document.get("elements") or []:
        regions = element.get("source_region_ids") or []
        if (element.get("type") != "table" or element.get("quality_status") == "passed"
                or len(regions) != 1 or regions[0] not in admitted):
            continue
        reasons = set(element.get("policy_reasons") or [])
        if not reasons <= TABLE_ADMITTABLE_REASONS:
            continue
        assertion = f"review_assertion:{registry['admission_id']}:{regions[0]}"
        element["quality_status"] = "passed"
        element["policy_reasons"] = [*element.get("policy_reasons", []),
                                     TABLE_ADMITTED_REASON, assertion]
        element["quality_issues"] = [*element.get("quality_issues", []), assertion]
        upgraded += 1
    return upgraded


def apply_ocr_document_admission(document: dict[str, Any],
                                 registry: dict[str, Any] | None) -> int:
    """Upgrade admissible OCR text elements and checked tables in place."""
    upgraded = apply_table_admission(document, registry)
    sha = str((document.get("source") or {}).get("sha256") or "")
    record = admission_for(registry, sha)
    if record is None:
        return upgraded
    assertion = f"review_assertion:{registry['admission_id']}:{sha[:12]}"
    for element in document.get("elements") or []:
        if element.get("type") != "text" or element.get("quality_status") == "passed":
            continue
        reasons = set(element.get("policy_reasons") or [])
        if "scanned_with_text_layer_requires_ocr_vote" in reasons:
            # On a scanned page with an embedded text layer the page-level
            # quarantine is copied into its regions; it is the same blocker,
            # resolved by the three-source vote, not an independent issue.
            reasons.discard("region_quality_quarantined")
        if not reasons or not reasons <= ADMITTABLE_REASONS:
            continue
        element["quality_status"] = "passed"
        element["policy_reasons"] = [*element.get("policy_reasons", []), ADMITTED_REASON]
        element["quality_issues"] = [*element.get("quality_issues", []), assertion]
        upgraded += 1
    return upgraded
