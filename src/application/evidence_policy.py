"""Evidence admission policy (T01, design D1-D3).

Single source of truth for the ``usage_policy`` / ``quality_status`` vocabulary
and the single-direction projection from Canonical elements to retrieval chunks.

Design (``docs/PDF_EXTRACTION_FREEZE_REMEDIATION_PLAN.md`` §2):

* D1 -- execution_status (completed/failed/...) is independent from
  quality_status. ``completed`` only means the engine ran; it does NOT imply
  ``passed``, correctness, or that the content may support an answer.
* D2 -- the admission policy is owned by the Canonical element and projected
  one-way to the chunk. A chunk's policy is the *strictest* among its source
  elements' policies. Enrichment, the retrieval adapter and the UI must never
  upgrade a policy.
* D3 -- structured content (table / formula / figure) defaults to
  ``source_locator_only`` until an explicit review/assertion upgrades it.

Strictness ordering::

    excluded > source_locator_only > answer_and_citation

Unknown / missing policy or quality always falls back to the safest
non-excluded option (``source_locator_only``); it never silently becomes
answer evidence.

This module deliberately consumes elements as plain dicts so the projection
works whether or not the element envelope has been extended with persistent
``quality_status`` / ``usage_policy`` fields (that persistence is T02's
lossless-provenance task). When the fields are absent the safe default is
``needs_review`` / ``source_locator_only`` for every element type -- a missing
status is never promoted to ``passed``, and "looks readable" is only a
chunk-level text floor, not a quality conclusion.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.ingestion.text_quality import is_answerable_text

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

ANSWER = "answer_and_citation"
LOCATOR = "source_locator_only"
EXCLUDED = "excluded"

_QUALITY_STATUSES = {"passed", "needs_review", "quarantined"}
_USAGE_POLICIES = {ANSWER, LOCATOR, EXCLUDED}

_POLICY_STRICTNESS = {ANSWER: 1, LOCATOR: 2, EXCLUDED: 3}
_QUALITY_STRICTNESS = {"passed": 0, "needs_review": 1, "quarantined": 2}

# Safest fallback when a policy / quality is missing or unknown. Never ANSWER.
DEFAULT_USAGE_POLICY = LOCATOR
DEFAULT_QUALITY_STATUS = "needs_review"

# Structured element types default to locator-only (D3).
_STRUCTURED_TYPES = {"table", "formula", "figure"}

# Reason codes (closed vocabulary) ----------------------------------------------------
REASON_STRUCTURED_DEFAULT = "structured_default_locator_only"
REASON_UNREADABLE_TEXT = "unreadable_text"
REASON_NEEDS_REVIEW_SOURCE = "needs_review_source"
REASON_QUARANTINED_SOURCE = "quarantined_source"
REASON_MISSING_POLICY = "missing_policy_default_locator_only"
REASON_MISSING_STRUCTURED_REVIEW = "missing_structured_review_assertion"
REASON_MISSING_SOURCE_ELEMENTS = "missing_source_elements"
# T09: a formula chunk whose anchor ([公式 N] + complete latex body) alone
# breaches the bge 512-token hard cap. The body is verbatim evidence and is
# never split to fit; the over-limit shard records this reason instead.
REASON_ANCHOR_OVERFLOW = "anchor_exceeds_token_cap"

# Fields that may carry an explicit human review / assertion binding on an
# element. A structured element may only become ``answer_and_citation`` (D3
# upgrade) when at least one of these is present and well-formed.
_REVIEW_ASSERTION_FIELDS = ("review_assertion_id", "review_id", "assertion_id")


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def normalize_policy(value: Any) -> str:
    """Map any value to a valid policy; unknown/missing -> ``LOCATOR``."""
    if value in _USAGE_POLICIES:
        return value  # type: ignore[return-value]
    return DEFAULT_USAGE_POLICY


def normalize_quality(value: Any) -> str:
    """Map any value to a valid quality status; unknown/missing -> ``needs_review``."""
    if value in _QUALITY_STATUSES:
        return value  # type: ignore[return-value]
    return DEFAULT_QUALITY_STATUS


def quality_to_policy(quality_status: Any) -> str:
    """Element quality -> default usage policy (D1).

    ``passed`` -> ``answer_and_citation``; ``needs_review`` / ``quarantined`` /
    missing -> ``source_locator_only`` (never answer evidence).
    """
    if normalize_quality(quality_status) == "passed":
        return ANSWER
    return LOCATOR


# --------------------------------------------------------------------------- #
# Strictest-wins aggregation
# --------------------------------------------------------------------------- #

def strictest_policy(policies: Iterable[Any]) -> str:
    """Return the strictest policy; missing/unknown entries count as ``LOCATOR``.

    An empty iterable yields ``LOCATOR`` (the safest non-excluded policy) -- a
    chunk with no source elements is never answer evidence (R4/R6).
    """
    best: str | None = None
    for p in policies:
        norm = normalize_policy(p)
        if best is None or _POLICY_STRICTNESS[norm] > _POLICY_STRICTNESS[best]:
            best = norm
    return best if best is not None else DEFAULT_USAGE_POLICY


def strictest_quality(qualities: Iterable[Any]) -> str:
    """Return the strictest quality; missing/unknown counts as ``needs_review``.

    An empty iterable yields ``needs_review`` -- a chunk with no source elements
    has no recorded quality and cannot be promoted to ``passed``.
    """
    best: str | None = None
    for q in qualities:
        norm = normalize_quality(q)
        if best is None or _QUALITY_STRICTNESS[norm] > _QUALITY_STRICTNESS[best]:
            best = norm
    return best if best is not None else DEFAULT_QUALITY_STATUS


# --------------------------------------------------------------------------- #
# Element-level resolution
# --------------------------------------------------------------------------- #

def _has_explicit(element: dict[str, Any], key: str) -> bool:
    """True when ``element[key]`` is present and normalises to a known value."""
    value = element.get(key)
    if value is None:
        return False
    if key == "usage_policy":
        return value in _USAGE_POLICIES
    if key == "quality_status":
        return value in _QUALITY_STATUSES
    return value is not None


def has_review_assertion(element: dict[str, Any]) -> bool:
    """True when the element is bound to a well-formed review/assertion ID (D3).

    A structured element may only be upgraded to ``answer_and_citation`` when it
    carries an explicit, non-empty review/assertion binding in one of the
    recognised fields. An empty string, whitespace, or a non-string value does
    not count -- there must be an actual identifier a human reviewer can trace.
    """
    for field in _REVIEW_ASSERTION_FIELDS:
        value = element.get(field)
        if isinstance(value, str) and value.strip():
            return True
    # Canonical convention (schema-enforced for formulas): the binding is a
    # ``review_assertion:<id>`` entry in policy_reasons / quality_issues.
    for field in ("policy_reasons", "quality_issues"):
        for entry in element.get(field) or []:
            if (isinstance(entry, str) and entry.startswith("review_assertion:")
                    and entry[len("review_assertion:"):].strip()):
                return True
    return False


def element_quality_status(element: dict[str, Any]) -> str:
    """Resolve one element's ``quality_status`` (normalised, safe default).

    A missing/unknown quality is **never** promoted to ``passed``: it falls back
    to ``needs_review`` for every element type (text included). ``passed`` is
    only valid when explicitly recorded -- "looks readable" is a text-floor
    check, not a quality conclusion. This is the T01 rule: quarantined,
    needs_review and *status-missing* sources all fail the answer gate.
    """
    q = element.get("quality_status")
    if q is not None:
        return normalize_quality(q)
    # No explicit quality recorded -> safest non-passed default for ALL types.
    return DEFAULT_QUALITY_STATUS  # "needs_review"


def element_usage_policy(element: dict[str, Any]) -> str:
    """Resolve one element's ``usage_policy`` via strictest synthesis.

    final = strictest(explicit usage_policy or safe default, quality->policy)

    The explicit policy and the quality-derived policy are BOTH evaluated and
    the strictest wins -- an explicit ``answer_and_citation`` cannot override a
    ``needs_review`` / ``quarantined`` / missing quality (P1 fix). When no
    explicit policy is recorded, the quality-derived policy stands alone; when
    quality is also missing, the safe default is ``source_locator_only``.

    Answer (``answer_and_citation``) is only possible here when the element's
    quality resolves to ``passed`` and no stricter explicit policy is set.
    Structured content (table/formula/figure) additionally requires an explicit
    review-approved policy **bound to a valid review/assertion ID** (D3) to
    answer -- ``passed`` + ``answer_and_citation`` without such a binding still
    resolves to ``source_locator_only``. A type-only default never answers.
    """
    quality = element_quality_status(element)
    quality_policy = quality_to_policy(quality)

    explicit = element.get("usage_policy")
    if _has_explicit(element, "usage_policy"):
        candidate = strictest_policy([normalize_policy(explicit), quality_policy])
    else:
        # No explicit policy: structured types default to locator-only (D3)
        # unless a review/assertion binding upgraded their quality to passed.
        if element.get("type") in _STRUCTURED_TYPES and not (
            quality_policy == ANSWER and has_review_assertion(element)
        ):
            return LOCATOR
        candidate = quality_policy

    # D3 gate: a structured element may only answer when it also carries a
    # valid review/assertion binding. Otherwise the answer candidate is clamped
    # down to locator-only, regardless of passed quality.
    if (
        candidate == ANSWER
        and element.get("type") in _STRUCTURED_TYPES
        and not has_review_assertion(element)
    ):
        return LOCATOR
    return candidate


def element_policy_reasons(element: dict[str, Any]) -> list[str]:
    """Return the element's recorded reason codes (copy)."""
    return list(element.get("policy_reasons") or [])


def _element_auto_reason(element: dict[str, Any], quality: str) -> str | None:
    """Auto-generated reason code explaining why an element blocks answering.

    Generated from the element's *effective* quality state (after safe-default
    resolution), not from the raw field, so a missing status is explained as
    such rather than silently treated as passed.
    """
    if quality == "quarantined":
        return REASON_QUARANTINED_SOURCE
    if quality == "needs_review":
        if not _has_explicit(element, "quality_status"):
            # quality was missing -> defaulted to needs_review.
            if element.get("type") in _STRUCTURED_TYPES:
                return REASON_STRUCTURED_DEFAULT
            return REASON_MISSING_POLICY
        return REASON_NEEDS_REVIEW_SOURCE
    # passed: structured but missing a review/assertion binding still blocks
    # answering (D3). Surface it so the downgrade is auditable.
    if (
        quality == "passed"
        and element.get("type") in _STRUCTURED_TYPES
        and not has_review_assertion(element)
    ):
        return REASON_MISSING_STRUCTURED_REVIEW
    return None


# --------------------------------------------------------------------------- #
# Chunk projection (strictest source wins)
# --------------------------------------------------------------------------- #

def chunk_usage_policy(
    elements: list[dict[str, Any]], text: str
) -> tuple[str, str, list[str]]:
    """Project source elements + chunk text to ``(policy, quality, reasons)``.

    Synthesis (T01):

        final_policy = strictest(
            explicit usage_policy or safe default,   # per element
            quality_status -> policy,                 # per element
            structured review-assertion gate (D3),    # per structured element
            text readability floor                    # chunk-level
        )

    ``answer_and_citation`` is only returned when ALL hold:
      * there is at least one source element (zero-source chunks are never
        answer evidence -- R4/R6);
      * every source element's ``quality_status`` resolves to ``passed``;
      * every source element's policy allows ``answer_and_citation``;
      * every structured source element carries a valid review/assertion ID
        bound to it (D3);
      * the chunk text passes the readability gate.

    Reason codes are auto-generated from each source element's effective state
    (plus any recorded element reasons), de-duplicated with stable first-seen
    ordering. ``missing_source_elements`` is produced for a zero-source chunk,
    ``missing_structured_review_assertion`` for an uncredentialed structured
    element, and ``unreadable_text`` when the text floor downgrades an
    otherwise-answerable chunk.
    """
    # Zero source elements: a chunk with nothing to trace cannot answer.
    if not elements:
        return LOCATOR, DEFAULT_QUALITY_STATUS, [REASON_MISSING_SOURCE_ELEMENTS]

    qualities = [element_quality_status(e) for e in elements]
    policies = [element_usage_policy(e) for e in elements]

    quality = strictest_quality(qualities)
    policy = strictest_policy(policies)

    # Reason codes: recorded element reasons + auto reasons, stable + deduped.
    seen: set[str] = set()
    reasons: list[str] = []
    for e, q in zip(elements, qualities):
        for r in element_policy_reasons(e):
            if r not in seen:
                seen.add(r)
                reasons.append(r)
        auto = _element_auto_reason(e, q)
        if auto and auto not in seen:
            seen.add(auto)
            reasons.append(auto)

    # Text readability floor: an unreadable chunk can never answer, regardless
    # of element quality.
    if policy == ANSWER and not is_answerable_text(text):
        policy = LOCATOR
        if quality == "passed":
            quality = "needs_review"
        if REASON_UNREADABLE_TEXT not in seen:
            reasons.append(REASON_UNREADABLE_TEXT)

    return policy, quality, reasons
