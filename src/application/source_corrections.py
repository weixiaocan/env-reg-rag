"""Owner-reviewed corrections of recognized formulas and text.

Recognition output is replaced only where the registry names the exact
original: the source PDF sha256, the physical page and the SHA-256 of the
recognized content (plus the bbox for formulas).  A correction that no
longer finds its original -- recognition was rerun, the page moved -- fails
the assembly instead of silently dropping out.

Each corrected element records the registry it came from in
``quality_issues``.  When the registry says the owner checked every
correction against the page (``owner_verified``), the corrected elements
also pass review with a ``review_assertion`` binding, so formulas may answer
as verified evidence; otherwise quality and policy stay as assembled.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path("data/registry/source-corrections.json")
_BBOX_TOLERANCE = 2.0
OWNER_VERIFIED_REASON = "owner_verified_correction"


class SourceCorrectionError(ValueError):
    """A correction does not match exactly one element of its document."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_source_corrections(path: str | Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.is_file():
        return None
    payload = path.read_bytes()
    registry = json.loads(payload)
    if registry.get("schema_version") != "1":
        raise SourceCorrectionError(f"unsupported source-corrections schema: {path}")
    registry["registry_sha256"] = hashlib.sha256(payload).hexdigest()
    return registry


def _primary_span(element: dict[str, Any]) -> dict[str, Any] | None:
    spans = element.get("source_spans") or []
    return next((s for s in spans if s.get("role") == "primary"), spans[0] if spans else None)


def _on_page(element: dict[str, Any], page: int) -> bool:
    span = _primary_span(element)
    return span is not None and span.get("physical_page") == page


def _bbox_close(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) == 4 and all(abs(x - y) <= _BBOX_TOLERANCE for x, y in zip(a, b))


def apply_source_corrections(document: dict[str, Any], registry: dict[str, Any] | None) -> int:
    """Apply the registry's corrections for this document in place."""
    if not registry:
        return 0
    sha = str((document.get("source") or {}).get("sha256") or "")
    marker = f"source_correction:{registry['registry_sha256'][:12]}:owner_reviewed"
    verified = registry.get("owner_verified") is True
    assertion = f"review_assertion:source-corrections:{registry['registry_sha256'][:12]}"

    def mark(element: dict[str, Any]) -> None:
        element["quality_issues"] = [*element.get("quality_issues", []), marker]
        if verified:
            element["quality_status"] = "passed"
            element["policy_reasons"] = [*element.get("policy_reasons", []),
                                         OWNER_VERIFIED_REASON, assertion]
            element["quality_issues"].append(assertion)
            # The structured outcome that produced a formula carries the same
            # verdict as its element (schema invariant).
            for outcome in document.get("structured_outcomes") or []:
                if outcome.get("element_id") == element.get("element_id")                         and outcome.get("kind") == element.get("type"):
                    outcome["quality_status"] = "passed"
                    outcome["issues"] = [*outcome.get("issues", []), assertion]
    elements = document.get("elements") or []
    applied = 0

    for fix in registry.get("formulas", []):
        if fix["source_sha256"] != sha:
            continue
        matches = [
            e for e in elements
            if e.get("type") == "formula"
            and _on_page(e, fix["physical_page"])
            and _sha256((e.get("content") or {}).get("latex") or "") == fix["original_latex_sha256"]
            and _bbox_close(_primary_span(e)["bbox"], fix["bbox"])
        ]
        if len(matches) != 1:
            raise SourceCorrectionError(
                f"formula correction on {sha[:12]} p{fix['physical_page']} matched "
                f"{len(matches)} elements (expected 1)")
        element = matches[0]
        element["content"]["latex"] = fix["corrected_latex"]
        mark(element)
        applied += 1

    for fix in registry.get("texts", []):
        if fix["source_sha256"] != sha:
            continue
        on_page = [e for e in elements if _on_page(e, fix["physical_page"])]
        texts = [
            e for e in on_page
            if e.get("type") == "text"
            and _sha256((e.get("content") or {}).get("text") or "") == fix["original_text_sha256"]
        ]
        if len(texts) != 1:
            raise SourceCorrectionError(
                f"text correction on {sha[:12]} p{fix['physical_page']} matched "
                f"{len(texts)} elements (expected 1)")
        texts[0]["content"]["text"] = fix["corrected_text"]
        mark(texts[0])
        # A formula may carry the same line as its "式中" context.
        for formula in on_page:
            content = formula.get("content") or {}
            if formula.get("type") == "formula" and content.get("context_text") \
                    and _sha256(content["context_text"]) == fix["original_text_sha256"]:
                content["context_text"] = fix["corrected_text"]
        applied += 1
    return applied
