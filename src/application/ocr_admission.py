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
    return {**registry, "registry_sha256": hashlib.sha256(raw).hexdigest()}


def admission_for(registry: dict[str, Any] | None, sha256: str) -> dict[str, Any] | None:
    if not registry:
        return None
    return next((record for record in registry["documents"]
                 if record["sha256"] == sha256 and record["verdict"] == "admit"), None)


def apply_ocr_document_admission(document: dict[str, Any],
                                 registry: dict[str, Any] | None) -> int:
    """Upgrade admissible OCR text elements in place; return the count."""
    sha = str((document.get("source") or {}).get("sha256") or "")
    record = admission_for(registry, sha)
    if record is None:
        return 0
    assertion = f"review_assertion:{registry['admission_id']}:{sha[:12]}"
    upgraded = 0
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
