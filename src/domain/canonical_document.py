"""V2 Canonical Document domain model (Element-owned, strong-typed content union).

Schema: ``v2.canonical/1.0``. See ``docs/v2-canonical-schema-design.md``.
The V1 Page-owned dataclasses are kept as ``Legacy*`` compatibility shims
until Phase 3 removes the legacy ingestion path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, get_args

SchemaVersion = Literal["v2.canonical/1.0"]
SCHEMA_VERSION: SchemaVersion = "v2.canonical/1.0"

ElementType = Literal["text", "table", "formula", "figure"]
TextRole = Literal[
    "document_title", "heading", "clause", "paragraph", "list_item",
    "header", "footer", "page_number", "watermark", "unknown",
]
SpanRole = Literal["primary", "continuation", "caption", "context", "note"]
ProvenanceOperation = Literal["detect", "extract", "recognize", "classify", "merge", "crop"]
ProvenanceMethod = Literal[
    "native_text", "ocr", "hybrid", "layout_detection",
    "table_recognition", "formula_recognition", "image_extraction", "unknown",
]
LinkType = Literal["continues", "references", "related"]
DocumentKind = Literal[
    "standard", "regulation", "policy", "guideline",
    "notice", "report", "other", "unknown",
]
EffectiveStatus = Literal[
    "current", "not_yet_effective", "expired",
    "repealed", "superseded", "draft", "unknown",
]
ParseStatus = Literal["parsed", "blank", "failed"]
CellRole = Literal["header", "stub", "data", "unknown"]

# T02 provenance/quality vocabulary carried on Page/Element envelopes. These
# fields are OPTIONAL (absent on pre-T02 artifacts) and EXCLUDED from the
# content/canonical digests (see _DIGEST_EXCLUDED_*_FIELDS) so that adding them
# never changes the identity of already-published artifacts.
ExtractionRoute = Literal["native", "full_ocr", "hybrid", "failed"]
PageDecisionStatus = Literal["approved", "quarantine", "failed"]
QualityStatus = Literal["passed", "needs_review", "quarantined"]

# Fields stripped before digesting: they describe *how* content was obtained,
# not the content itself, so the same text has the same content_id whether it
# was native or OCR, passed or quarantined (T02 §7 determinism + compat).
# Note: ``canonical_content_id`` excludes these (content identity), but
# ``canonical_id`` (audit identity) re-includes them when present via
# ``_audit_payload`` so a quality/provenance change on a NEW artifact produces a
# new audit identity (R5). Legacy artifacts without the fields keep their old
# canonical_id because the audit payload is empty.
_DIGEST_EXCLUDED_PAGE_FIELDS = {
    "extraction_route", "decision_status", "decision_reasons",
    "engine_id", "confidence", "config_hash",
}
_DIGEST_EXCLUDED_ELEMENT_FIELDS = {
    "quality_status", "policy_reasons",
    "source_region_ids", "processing_config_ids", "quality_issues",
    "execution_status", "consumed_sources",
    # T04-R2: formula lossless provenance (audit-only). crop_assets carries the
    # REAL crop asset identity (ref + sha256) when the upstream recorded one;
    # cross_page_relations carries the structured cross-page parameter/condition
    # relations verbatim. Both are excluded from content identity and included
    # in the canonical audit identity.
    "crop_assets", "cross_page_relations",
    # T09: formula/explanation association (audit-only, digest-excluded).
    # explanation_bindings records the element-level unique binding between a
    # formula and its "式中" explanation text elements; a binding change flips
    # canonical_id via the audit while content_id stays content-only.
    "explanation_bindings",
    # T5: independent OCR fusion decisions are provenance/audit metadata.  The
    # fused text itself remains content-addressed; this record explains how it
    # was selected without changing the established ID inputs.
    "ocr_fusion",
    "ocr_text_sources",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class SchemaError(ValueError):
    """Raised when a dict does not conform to the V2 Canonical Schema."""


def _require_keys(
    data: dict[str, Any],
    keys: set[str],
    where: str,
    *,
    optional: set[str] | None = None,
) -> None:
    """Closed-schema check: ``keys`` are required, ``optional`` are allowed-but-absent.

    Unknown keys (anything not in ``keys | optional``) are rejected so the
    schema stays closed. T02 extends Page/Element with optional provenance /
    quality fields: they are absent on pre-T02 artifacts (safe default applies)
    and present on new candidates, but foreign keys still raise.
    """
    missing = sorted(keys - set(data))
    if missing:
        raise SchemaError(f"{where}: missing required keys {missing}")
    allowed = keys | (optional or set())
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SchemaError(f"{where}: unknown keys {unknown}")


def _check_literal(value: Any, literal: Any, where: str) -> None:
    allowed = set(get_args(literal))
    if value not in allowed:
        raise SchemaError(f"{where}: expected one of {sorted(allowed)}, got {value!r}")


def _sha256_hex(value: str, where: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise SchemaError(f"{where}: expected 64 lowercase hex, got {value!r}")


def _validate_crop_asset_entries(assets: Any, where: str) -> None:
    """Closed validation for crop-asset provenance entries (T04-R2/R3).

    Each entry is a REAL crop asset reference: {"ref": <path>, "sha256": <hex>}.
    Shared by the FormulaElement envelope and the formula outcome ledger so
    both sides of the completed-pair carry the same closed shape.
    """
    if not isinstance(assets, list):
        raise SchemaError(f"{where} must be a list")
    for c in assets:
        if not isinstance(c, dict):
            raise SchemaError(f"{where} entries must be objects")
        _require_keys(c, {"ref", "sha256"}, f"{where}[]")
        if not isinstance(c["ref"], str) or not c["ref"]:
            raise SchemaError(f"{where}[].ref must be a non-empty string")
        _sha256_hex(c["sha256"], f"{where}[].sha256")


_EXPLANATION_BINDING_KEYS = {
    "element_id", "region_id", "basis", "role", "order",
}
_EXPLANATION_BINDING_BASES = {"below_geometry", "explicit_number_reference"}
_EXPLANATION_BINDING_ROLES = {"primary", "secondary"}


def _validate_explanation_bindings(bindings: Any, where: str) -> None:
    """Closed validation for one formula's explanation-binding records (T09).

    Cross-formula uniqueness (an explanation element is PRIMARY to exactly one
    formula) and target existence/type are document-level invariants enforced
    in ``CanonicalDocument.validate``; this helper only closes the per-entry
    shape so no foreign key can smuggle an unreviewed basis through.
    """
    if not isinstance(bindings, list):
        raise SchemaError(f"{where} must be a list")
    for b in bindings:
        if not isinstance(b, dict):
            raise SchemaError(f"{where} entries must be objects")
        _require_keys(b, _EXPLANATION_BINDING_KEYS, f"{where}[]")
        if not isinstance(b["element_id"], str) or not b["element_id"].strip():
            raise SchemaError(f"{where}[].element_id must be a non-empty string")
        if b["region_id"] is not None and not isinstance(b["region_id"], str):
            raise SchemaError(f"{where}[].region_id must be a string or null")
        if b["basis"] not in _EXPLANATION_BINDING_BASES:
            raise SchemaError(
                f"{where}[].basis must be one of "
                f"{sorted(_EXPLANATION_BINDING_BASES)}, got {b['basis']!r}")
        if b["role"] not in _EXPLANATION_BINDING_ROLES:
            raise SchemaError(
                f"{where}[].role must be one of "
                f"{sorted(_EXPLANATION_BINDING_ROLES)}, got {b['role']!r}")
        if type(b["order"]) is not int or b["order"] < 0:
            raise SchemaError(
                f"{where}[].order must be a non-negative int, got {b['order']!r}")


def _validate_ocr_fusion(records: Any, where: str) -> None:
    """Validate the closed audit record for an inserted/replaced OCR line."""
    if not isinstance(records, list):
        raise SchemaError(f"{where} must be a list")
    required = {
        "action", "engine", "run_id", "run_manifest_sha256", "rule_version",
        "original_primary_text", "independent_text", "independent_line_id",
        "independent_score", "alignment_score", "source_region_ids",
    }
    for record in records:
        if not isinstance(record, dict):
            raise SchemaError(f"{where} entries must be objects")
        _require_keys(record, required, f"{where}[]")
        if record["action"] not in {"insert", "replace"}:
            raise SchemaError(f"{where}[].action must be insert or replace")
        for key in ("engine", "run_id", "rule_version", "independent_text",
                    "independent_line_id"):
            if not isinstance(record[key], str) or not record[key]:
                raise SchemaError(f"{where}[].{key} must be a non-empty string")
        _sha256_hex(record["run_manifest_sha256"], f"{where}[].run_manifest_sha256")
        original = record["original_primary_text"]
        if original is not None and not isinstance(original, str):
            raise SchemaError(f"{where}[].original_primary_text must be a string or null")
        for key in ("independent_score", "alignment_score"):
            value = record[key]
            if value is not None and (not isinstance(value, (int, float))
                                      or isinstance(value, bool) or not 0 <= value <= 1):
                raise SchemaError(f"{where}[].{key} must be in [0,1] or null")
        if not isinstance(record["source_region_ids"], list) or not all(
                isinstance(v, str) for v in record["source_region_ids"]):
            raise SchemaError(f"{where}[].source_region_ids must be a string list")


def _validate_ocr_text_sources(records: Any, where: str) -> None:
    """Validate PP-first and primary-fallback line provenance records."""
    if not isinstance(records, list):
        raise SchemaError(f"{where} must be a list")
    required = {
        "source", "line_id", "score", "run_id", "run_manifest_sha256",
        "preprocessing", "primary_prefix_preserved",
    }
    for record in records:
        if not isinstance(record, dict):
            raise SchemaError(f"{where} entries must be objects")
        _require_keys(record, required, f"{where}[]")
        if record["source"] not in {"pp_clean", "primary_fallback"}:
            raise SchemaError(f"{where}[].source is invalid")
        for key in ("line_id", "run_id"):
            if not isinstance(record[key], str) or not record[key]:
                raise SchemaError(f"{where}[].{key} must be a non-empty string")
        _sha256_hex(record["run_manifest_sha256"], f"{where}[].run_manifest_sha256")
        score = record["score"]
        if score is not None and (not isinstance(score, (int, float))
                                  or isinstance(score, bool) or not 0 <= score <= 1):
            raise SchemaError(f"{where}[].score must be in [0,1] or null")
        if not isinstance(record["preprocessing"], dict):
            raise SchemaError(f"{where}[].preprocessing must be an object")
        if not isinstance(record["primary_prefix_preserved"], bool):
            raise SchemaError(f"{where}[].primary_prefix_preserved must be bool")


def _validate_relation_records(relations: Any, where: str) -> None:
    """Closed validation for structured cross-page relation records.

    Shared by the FormulaElement envelope and the formula outcome ledger
    (T04-R3): kind / basis / verified / association_status plus lossless
    source/target side blocks and the span/link projection ids.
    """
    if not isinstance(relations, list):
        raise SchemaError(f"{where} must be a list")
    for rel in relations:
        if not isinstance(rel, dict):
            raise SchemaError(f"{where} entries must be objects")
        _require_keys(rel, {"kind", "basis", "verified",
                            "association_status", "source", "target",
                            "source_span_id", "target_element_id"},
                      f"{where}[]", optional={"relation_id"})
        if not isinstance(rel["kind"], str) or not rel["kind"]:
            raise SchemaError(f"{where}[].kind must be a non-empty string")
        if not isinstance(rel["verified"], bool) and rel["verified"] is not None:
            raise SchemaError(f"{where}[].verified must be a bool or null")
        for k in ("basis", "association_status", "source_span_id",
                  "target_element_id"):
            v = rel[k]
            if v is not None and not isinstance(v, str):
                raise SchemaError(f"{where}[].{k} must be a string or null")
        for k in ("source", "target"):
            side = rel[k]
            if side is None:
                continue
            if not isinstance(side, dict):
                raise SchemaError(f"{where}[].{k} must be an object or null")
            _require_keys(side, {"region_id", "element_id", "physical_page",
                                 "bbox", "text", "formula_number"},
                          f"{where}[].{k}")
            if side["bbox"] is not None:
                bb = side["bbox"]
                if (not isinstance(bb, list) or len(bb) != 4
                        or not all(isinstance(v, (int, float))
                                   and not isinstance(v, bool) for v in bb)):
                    raise SchemaError(
                        f"{where}[].{k}.bbox must be a 4-number list or null")
            for k2 in ("region_id", "element_id", "text", "formula_number"):
                v2 = side[k2]
                if v2 is not None and not isinstance(v2, str):
                    raise SchemaError(
                        f"{where}[].{k}.{k2} must be a string or null")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------- #
# Document source / metadata / generation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DocumentSource:
    sha256: str
    file_name: str
    media_type: str
    page_count: int
    source_uri: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentSource":
        _require_keys(data, {"sha256", "file_name", "media_type", "page_count", "source_uri"}, "source")
        _sha256_hex(data["sha256"], "source.sha256")
        if data["page_count"] <= 0:
            raise SchemaError("source.page_count must be positive")
        return cls(
            sha256=data["sha256"].lower(),
            file_name=data["file_name"],
            media_type=data["media_type"],
            page_count=data["page_count"],
            source_uri=data["source_uri"],
        )


@dataclass(frozen=True)
class Identifier:
    scheme: str
    value: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Identifier":
        _require_keys(data, {"scheme", "value"}, "metadata.identifiers[]")
        return cls(scheme=data["scheme"], value=data["value"])


@dataclass(frozen=True)
class DocumentMetadata:
    title: str
    alternate_titles: list[str]
    language: str
    identifiers: list[Identifier]
    document_kind: DocumentKind
    jurisdictions: list[str]
    issuing_authorities: list[str]
    publication_date: str | None
    effective_from: str | None
    effective_to: str | None
    effective_status: EffectiveStatus
    effective_status_as_of: str | None
    registry_snapshot_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentMetadata":
        _require_keys(data, {
            "title", "alternate_titles", "language", "identifiers",
            "document_kind", "jurisdictions", "issuing_authorities",
            "publication_date", "effective_from", "effective_to",
            "effective_status", "effective_status_as_of", "registry_snapshot_id",
        }, "metadata")
        _check_literal(data["document_kind"], DocumentKind, "metadata.document_kind")
        _check_literal(data["effective_status"], EffectiveStatus, "metadata.effective_status")
        if not isinstance(data["title"], str) or not data["title"]:
            raise SchemaError("metadata.title must be a non-empty string")
        if data["effective_status"] != "unknown" and not data["effective_status_as_of"]:
            raise SchemaError("metadata.effective_status_as_of required when status != unknown")
        return cls(
            title=data["title"],
            alternate_titles=list(data["alternate_titles"]),
            language=data["language"],
            identifiers=[Identifier.from_dict(i) for i in data["identifiers"]],
            document_kind=data["document_kind"],
            jurisdictions=list(data["jurisdictions"]),
            issuing_authorities=list(data["issuing_authorities"]),
            publication_date=data["publication_date"],
            effective_from=data["effective_from"],
            effective_to=data["effective_to"],
            effective_status=data["effective_status"],
            effective_status_as_of=data["effective_status_as_of"],
            registry_snapshot_id=data["registry_snapshot_id"],
        )


@dataclass(frozen=True)
class Engine:
    engine_id: str
    name: str
    version: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Engine":
        _require_keys(data, {"engine_id", "name", "version"}, "generation.engines[]")
        return cls(engine_id=data["engine_id"], name=data["name"], version=data["version"])


@dataclass(frozen=True)
class Generation:
    run_id: str
    created_at: str
    pipeline: str
    pipeline_version: str
    config_sha256: str
    engines: list[Engine]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Generation":
        _require_keys(data, {
            "run_id", "created_at", "pipeline", "pipeline_version",
            "config_sha256", "engines",
        }, "generation")
        _sha256_hex(data["config_sha256"], "generation.config_sha256")
        return cls(
            run_id=data["run_id"],
            created_at=data["created_at"],
            pipeline=data["pipeline"],
            pipeline_version=data["pipeline_version"],
            config_sha256=data["config_sha256"],
            engines=[Engine.from_dict(e) for e in data["engines"]],
        )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ParseError:
    code: str
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParseError":
        _require_keys(data, {"code", "message"}, "pages[].parse_error")
        return cls(code=data["code"], message=data["message"])


@dataclass(frozen=True)
class Page:
    physical_page: int
    printed_label: str | None
    width: float
    height: float
    unit: str
    rotation_degrees: int
    parse_status: ParseStatus
    parse_error: ParseError | None
    # T02 page-level provenance (optional, digest-excluded from content_id;
    # included in canonical_id audit when present). Absent on pre-T02 artifacts;
    # present on new candidates so a chunk can trace back to the page's
    # extraction route, decision, engine, confidence and config without
    # re-reading page-intermediate -- including on blank pages that carry no
    # elements (P2: blank-page provenance must not be lost).
    extraction_route: ExtractionRoute | None = None
    decision_status: PageDecisionStatus | None = None
    decision_reasons: list[str] = field(default_factory=list)
    engine_id: str | None = None
    confidence: float | None = None
    config_hash: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Page":
        _require_keys(
            data,
            {
                "physical_page", "printed_label", "width", "height",
                "unit", "rotation_degrees", "parse_status", "parse_error",
            },
            "pages[]",
            optional={
                "extraction_route", "decision_status", "decision_reasons",
                "engine_id", "confidence", "config_hash",
            },
        )
        _check_literal(data["parse_status"], ParseStatus, "pages[].parse_status")
        if data["rotation_degrees"] not in (0, 90, 180, 270):
            raise SchemaError("pages[].rotation_degrees must be 0/90/180/270")
        if data["width"] <= 0 or data["height"] <= 0:
            raise SchemaError("pages[].width/height must be positive")
        if data["parse_status"] == "failed" and data["parse_error"] is None:
            raise SchemaError("pages[].parse_error required when parse_status=failed")
        if data["parse_status"] != "failed" and data["parse_error"] is not None:
            raise SchemaError("pages[].parse_error must be null unless parse_status=failed")
        route = data.get("extraction_route")
        if route is not None:
            _check_literal(route, ExtractionRoute, "pages[].extraction_route")
        decision = data.get("decision_status")
        if decision is not None:
            _check_literal(decision, PageDecisionStatus, "pages[].decision_status")
        reasons = data.get("decision_reasons")
        if reasons is not None and not isinstance(reasons, list):
            raise SchemaError("pages[].decision_reasons must be a list")
        engine_id = data.get("engine_id")
        if engine_id is not None and not isinstance(engine_id, str):
            raise SchemaError("pages[].engine_id must be a string")
        confidence = data.get("confidence")
        if confidence is not None and not isinstance(confidence, (int, float)):
            raise SchemaError("pages[].confidence must be a number or null")
        if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
            raise SchemaError("pages[].confidence must be in [0,1] or null")
        config_hash = data.get("config_hash")
        if config_hash is not None and (
            not isinstance(config_hash, str) or len(config_hash) != 64
            or any(c not in "0123456789abcdef" for c in config_hash.lower())
        ):
            raise SchemaError("pages[].config_hash must be 64 lowercase hex or null")
        return cls(
            physical_page=data["physical_page"],
            printed_label=data["printed_label"],
            width=data["width"],
            height=data["height"],
            unit=data["unit"],
            rotation_degrees=data["rotation_degrees"],
            parse_status=data["parse_status"],
            parse_error=ParseError.from_dict(data["parse_error"]) if data["parse_error"] else None,
            extraction_route=route,
            decision_status=decision,
            decision_reasons=list(reasons) if reasons is not None else [],
            engine_id=engine_id,
            confidence=float(confidence) if confidence is not None else None,
            config_hash=config_hash.lower() if isinstance(config_hash, str) else None,
        )


# --------------------------------------------------------------------------- #
# SourceSpan / Provenance / Link
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourceSpan:
    span_id: str
    role: SpanRole
    physical_page: int
    bbox: list[float]
    orientation_degrees: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceSpan":
        _require_keys(data, {
            "span_id", "role", "physical_page", "bbox", "orientation_degrees",
        }, "source_spans[]")
        _check_literal(data["role"], SpanRole, "source_spans[].role")
        bbox = list(data["bbox"])
        if len(bbox) != 4:
            raise SchemaError("source_spans[].bbox must have 4 floats")
        x0, y0, x1, y1 = bbox
        if not (x0 <= x1 and y0 <= y1):
            raise SchemaError("source_spans[].bbox must satisfy x0<=x1 and y0<=y1")
        return cls(
            span_id=data["span_id"],
            role=data["role"],
            physical_page=data["physical_page"],
            bbox=bbox,
            orientation_degrees=data["orientation_degrees"],
        )


@dataclass(frozen=True)
class Provenance:
    operation: ProvenanceOperation
    method: ProvenanceMethod
    engine_id: str
    confidence: float | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provenance":
        _require_keys(data, {"operation", "method", "engine_id", "confidence"}, "provenance[]")
        _check_literal(data["operation"], ProvenanceOperation, "provenance[].operation")
        _check_literal(data["method"], ProvenanceMethod, "provenance[].method")
        if data["confidence"] is not None and not (0.0 <= data["confidence"] <= 1.0):
            raise SchemaError("provenance[].confidence must be in [0,1] or null")
        return cls(
            operation=data["operation"],
            method=data["method"],
            engine_id=data["engine_id"],
            confidence=data["confidence"],
        )


@dataclass(frozen=True)
class Link:
    type: LinkType
    target_element_id: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Link":
        _require_keys(data, {"type", "target_element_id"}, "links[]")
        _check_literal(data["type"], LinkType, "links[].type")
        return cls(type=data["type"], target_element_id=data["target_element_id"])


# --------------------------------------------------------------------------- #
# Discriminated content union
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ListInfo:
    marker: str
    level: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ListInfo":
        _require_keys(data, {"marker", "level"}, "content.list")
        if data["level"] < 0:
            raise SchemaError("content.list.level must be >= 0")
        return cls(marker=data["marker"], level=data["level"])


@dataclass(frozen=True)
class TextContent:
    text: str
    list: ListInfo | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TextContent":
        _require_keys(data, {"text", "list"}, "content(text)")
        return cls(text=data["text"], list=ListInfo.from_dict(data["list"]) if data["list"] else None)


@dataclass(frozen=True)
class TableCell:
    row: int
    column: int
    row_span: int
    column_span: int
    role: CellRole
    text: str
    source_span_ids: list[str]
    # T03 optional cell-level provenance (absent on pre-T03 artifacts). These
    # are content fields (a changed cell bbox/confidence is a content change)
    # but are omitted from the digest when absent so legacy artifacts keep an
    # byte-identical content_id (see _content_payload / _table_cell_dict).
    source_bbox: list[float] | None = None
    confidence: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableCell":
        _require_keys(data, {
            "row", "column", "row_span", "column_span",
            "role", "text", "source_span_ids",
        }, "content.cells[]", optional={"source_bbox", "confidence"})
        _check_literal(data["role"], CellRole, "content.cells[].role")
        if data["row_span"] < 1 or data["column_span"] < 1:
            raise SchemaError("content.cells[].row_span/column_span must be >= 1")
        sb = data.get("source_bbox")
        if sb is not None and (not isinstance(sb, list) or len(sb) != 4):
            raise SchemaError("content.cells[].source_bbox must be a 4-list or null")
        conf = data.get("confidence")
        if conf is not None and not isinstance(conf, (int, float)):
            raise SchemaError("content.cells[].confidence must be a number or null")
        return cls(
            row=data["row"],
            column=data["column"],
            row_span=data["row_span"],
            column_span=data["column_span"],
            role=data["role"],
            text=data["text"],
            source_span_ids=list(data["source_span_ids"]),
            source_bbox=list(sb) if sb is not None else None,
            confidence=float(conf) if conf is not None else None,
        )

    def to_digest_dict(self) -> dict[str, Any]:
        """Digest form: omit absent optional fields (legacy byte-identical)."""
        d: dict[str, Any] = {
            "row": self.row, "column": self.column,
            "row_span": self.row_span, "column_span": self.column_span,
            "role": self.role, "text": self.text,
            "source_span_ids": list(self.source_span_ids),
        }
        if self.source_bbox is not None:
            d["source_bbox"] = list(self.source_bbox)
        if self.confidence is not None:
            d["confidence"] = self.confidence
        return d


@dataclass(frozen=True)
class TableNote:
    text: str
    source_span_ids: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableNote":
        _require_keys(data, {"text", "source_span_ids"}, "content.notes[]")
        return cls(text=data["text"], source_span_ids=list(data["source_span_ids"]))


@dataclass(frozen=True)
class TableContent:
    row_count: int
    column_count: int
    caption: str | None
    cells: list[TableCell]
    notes: list[TableNote]
    # T03 optional table-level content (absent on pre-T03 artifacts). Omitted
    # from the digest when absent so legacy artifacts keep a byte-identical
    # content_id; present on new artifacts so a caption/number/unit change is a
    # real content-identity change (R5).
    table_number: str | None = None
    unit_context: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableContent":
        _require_keys(data, {"row_count", "column_count", "caption", "cells", "notes"},
                      "content(table)", optional={"table_number", "unit_context"})
        if data["row_count"] <= 0 or data["column_count"] <= 0:
            raise SchemaError("content.row_count/column_count must be positive")
        tn = data.get("table_number")
        if tn is not None and not isinstance(tn, str):
            raise SchemaError("content.table_number must be a string or null")
        uc = data.get("unit_context")
        if uc is not None and not isinstance(uc, list):
            raise SchemaError("content.unit_context must be a list")
        return cls(
            row_count=data["row_count"],
            column_count=data["column_count"],
            caption=data["caption"],
            cells=[TableCell.from_dict(c) for c in data["cells"]],
            notes=[TableNote.from_dict(n) for n in data["notes"]],
            table_number=tn,
            unit_context=list(uc) if uc is not None else [],
        )

    def to_digest_dict(self) -> dict[str, Any]:
        """Digest form: omit absent optional content fields (legacy compat)."""
        d: dict[str, Any] = {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "caption": self.caption,
            "cells": [c.to_digest_dict() for c in self.cells],
            "notes": [asdict(n) for n in self.notes],
        }
        if self.table_number is not None:
            d["table_number"] = self.table_number
        if self.unit_context:
            d["unit_context"] = [dict(u) for u in self.unit_context]
        return d


@dataclass(frozen=True)
class FormulaContent:
    formula_number: str | None
    latex: str | None
    recognized_formula: str | None
    context_text: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FormulaContent":
        _require_keys(data, {
            "formula_number", "latex", "recognized_formula", "context_text",
        }, "content(formula)")
        return cls(
            formula_number=data["formula_number"],
            latex=data["latex"],
            recognized_formula=data["recognized_formula"],
            context_text=data["context_text"],
        )


@dataclass(frozen=True)
class FigureReference:
    text: str
    source_span_ids: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FigureReference":
        _require_keys(data, {"text", "source_span_ids"}, "content.references[]")
        return cls(text=data["text"], source_span_ids=list(data["source_span_ids"]))


@dataclass(frozen=True)
class FigureAsset:
    ref: str
    sha256: str
    media_type: str
    pixel_width: int
    pixel_height: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FigureAsset":
        _require_keys(data, {
            "ref", "sha256", "media_type", "pixel_width", "pixel_height",
        }, "content.asset")
        _sha256_hex(data["sha256"], "content.asset.sha256")
        if data["pixel_width"] <= 0 or data["pixel_height"] <= 0:
            raise SchemaError("content.asset.pixel_width/height must be positive")
        return cls(
            ref=data["ref"],
            sha256=data["sha256"].lower(),
            media_type=data["media_type"],
            pixel_width=data["pixel_width"],
            pixel_height=data["pixel_height"],
        )


@dataclass(frozen=True)
class FigureContent:
    caption: str | None
    references: list[FigureReference]
    asset: FigureAsset | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FigureContent":
        _require_keys(data, {"caption", "references", "asset"}, "content(figure)")
        return cls(
            caption=data["caption"],
            references=[FigureReference.from_dict(r) for r in data["references"]],
            asset=FigureAsset.from_dict(data["asset"]) if data["asset"] else None,
        )


_CONTENT_DISPATCH: dict[str, Any] = {
    "text": TextContent,
    "table": TableContent,
    "formula": FormulaContent,
    "figure": FigureContent,
}


# --------------------------------------------------------------------------- #
# Element envelope
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SectionPathEntry:
    label: str
    title: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionPathEntry":
        _require_keys(data, {"label", "title"}, "section_path[]")
        return cls(label=data["label"], title=data["title"])


@dataclass(frozen=True)
class Element:
    element_id: str
    type: ElementType
    role: str | None
    label: str | None
    section_path: list[SectionPathEntry]
    content: TextContent | TableContent | FormulaContent | FigureContent
    source_spans: list[SourceSpan]
    provenance: list[Provenance]
    links: list[Link]
    # T02 element-level quality/provenance (optional, digest-excluded from
    # content_id; included in canonical_id audit when present). Absent on
    # pre-T02 artifacts; present on new candidates so the chunker's strictest
    # source-wins projection (T01) reads a real quality verdict instead of
    # falling back to needs_review for every element. Never fabricated as
    # ``passed`` without a machine rule (route+readability+region+decision).
    # source_region_ids / processing_config_ids / quality_issues carry the
    # upstream region identity and issues losslessly (R6 traceability). The
    # plural list form preserves EVERY participating region when an element
    # overlaps multiple upstream text regions (strictest-wins over all).
    quality_status: QualityStatus | None = None
    policy_reasons: list[str] = field(default_factory=list)
    source_region_ids: list[str] = field(default_factory=list)
    processing_config_ids: list[str] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    # T03 structured-element execution status (optional, digest-excluded from
    # content_id; included in canonical_id audit when present). Absent on
    # pre-T03 artifacts. Carries the upstream recognition execution outcome
    # (completed/failed/unsupported) so a status change flips canonical_id
    # without changing content_id (R5).
    execution_status: str | None = None
    # T03 rework: identity of the plain-text elements CONSUMED by this
    # structured element during text/table dedup -- the upstream element id,
    # physical page and bbox of each consumed text element. Optional,
    # digest-excluded provenance (T02 pattern): a consumed-source change flips
    # canonical_id via the audit while content_id stays content-only and
    # legacy artifacts remain byte-identical. Preserving this identity is what
    # makes the dedup traceable instead of a silent drop.
    consumed_sources: list[dict[str, Any]] = field(default_factory=list)
    # T04-R2: formula crop-asset provenance (optional, digest-excluded). Each
    # entry is the REAL crop asset the recognition consumed, verbatim from the
    # page-intermediate: {"ref": <path>, "sha256": <hex>}. Empty when the
    # upstream recorded no asset -- never fabricated, never a bbox posing as a
    # crop identity (that is an explicit ``missing_crop_asset`` issue instead).
    crop_assets: list[dict[str, Any]] = field(default_factory=list)
    # T04-R2: structured cross-page parameter/condition relations (optional,
    # digest-excluded). One closed record per upstream relation, preserving
    # kind/basis/verified/association_status and both side blocks (region, page,
    # bbox, formula number, parameter/condition text) verbatim. Unconfirmed or
    # unresolvable relations stay needs_review -- never adjacency guessing.
    cross_page_relations: list[dict[str, Any]] = field(default_factory=list)
    # T09: formula/explanation association (optional, digest-excluded). One
    # closed record per bound explanation element: {element_id, region_id,
    # basis, role, order}. ``basis`` is the EVIDENCE the binding rests on
    # (below_geometry = contiguous same-section explanation block directly
    # below the formula opened by an explicit "式中" signal;
    # explicit_number_reference = a unique numbered cross-page marker), never
    # adjacency guessing. An ambiguous situation produces NO binding at all --
    # the explanation stays an independent text element and the ambiguity is
    # recorded as a quality issue on the formula.
    explanation_bindings: list[dict[str, Any]] = field(default_factory=list)
    # T5 optional, closed independent-OCR fusion record(s).  One text element
    # currently has at most one record, but a list keeps the provenance chain
    # explicit and avoids hiding a future staged merge.
    ocr_fusion: list[dict[str, Any]] = field(default_factory=list)
    # R1c PP-first OCR line provenance; immutable run and preprocessing
    # configuration remain audit metadata rather than content identity.
    ocr_text_sources: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Element":
        _require_keys(
            data,
            {
                "element_id", "type", "role", "label", "section_path",
                "content", "source_spans", "provenance", "links",
            },
            f"element[{data.get('element_id', '?')}]",
            optional={
                "quality_status", "policy_reasons",
                "source_region_ids", "processing_config_ids", "quality_issues",
                "execution_status", "consumed_sources",
                "crop_assets", "cross_page_relations",
                "explanation_bindings",
                "ocr_fusion",
                "ocr_text_sources",
            },
        )
        etype = data["type"]
        _check_literal(etype, ElementType, "element.type")
        content_cls = _CONTENT_DISPATCH[etype]
        quality = data.get("quality_status")
        if quality is not None:
            _check_literal(quality, QualityStatus, "element.quality_status")
        for list_field in ("policy_reasons", "quality_issues",
                           "source_region_ids", "processing_config_ids"):
            val = data.get(list_field)
            if val is not None and not isinstance(val, list):
                raise SchemaError(f"element.{list_field} must be a list")
        exec_status = data.get("execution_status")
        if exec_status is not None and not isinstance(exec_status, str):
            raise SchemaError("element.execution_status must be a string or null")
        consumed = data.get("consumed_sources")
        if consumed is not None:
            if not isinstance(consumed, list):
                raise SchemaError("element.consumed_sources must be a list")
            for c in consumed:
                if not isinstance(c, dict):
                    raise SchemaError("element.consumed_sources entries must be objects")
                # T05-R2: "basis" records the explicit identity a figure
                # caption binding relied on ("element_id"/"bbox"); audit-only
                # (digest-excluded), absent on table/formula dedup entries.
                _require_keys(c, {"element_id", "physical_page", "bbox"},
                              "element.consumed_sources[]",
                              optional={"basis"})
        assets = data.get("crop_assets")
        if assets is not None:
            _validate_crop_asset_entries(assets, "element.crop_assets")
        relations = data.get("cross_page_relations")
        if relations is not None:
            _validate_relation_records(relations, "element.cross_page_relations")
        bindings = data.get("explanation_bindings")
        if bindings is not None:
            _validate_explanation_bindings(bindings, "element.explanation_bindings")
        fusion = data.get("ocr_fusion")
        if fusion is not None:
            _validate_ocr_fusion(fusion, "element.ocr_fusion")
        text_sources = data.get("ocr_text_sources")
        if text_sources is not None:
            _validate_ocr_text_sources(text_sources, "element.ocr_text_sources")
        return cls(
            element_id=data["element_id"],
            type=etype,
            role=data["role"],
            label=data["label"],
            section_path=[SectionPathEntry.from_dict(s) for s in data["section_path"]],
            content=content_cls.from_dict(data["content"]),
            source_spans=[SourceSpan.from_dict(s) for s in data["source_spans"]],
            provenance=[Provenance.from_dict(p) for p in data["provenance"]],
            links=[Link.from_dict(l) for l in data["links"]],
            quality_status=quality,
            policy_reasons=list(data["policy_reasons"]) if data.get("policy_reasons") is not None else [],
            source_region_ids=list(data["source_region_ids"]) if data.get("source_region_ids") is not None else [],
            processing_config_ids=list(data["processing_config_ids"]) if data.get("processing_config_ids") is not None else [],
            quality_issues=list(data["quality_issues"]) if data.get("quality_issues") is not None else [],
            execution_status=exec_status,
            consumed_sources=[dict(c) for c in data["consumed_sources"]] if data.get("consumed_sources") is not None else [],
            crop_assets=[dict(c) for c in data["crop_assets"]] if data.get("crop_assets") is not None else [],
            cross_page_relations=[dict(r) for r in data["cross_page_relations"]] if data.get("cross_page_relations") is not None else [],
            explanation_bindings=[dict(b) for b in data["explanation_bindings"]] if data.get("explanation_bindings") is not None else [],
            ocr_fusion=[dict(f) for f in data["ocr_fusion"]] if data.get("ocr_fusion") is not None else [],
            ocr_text_sources=[dict(s) for s in data["ocr_text_sources"]]
            if data.get("ocr_text_sources") is not None else [],
        )


# --------------------------------------------------------------------------- #
# CanonicalDocument
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CanonicalDocument:
    schema_version: SchemaVersion
    canonical_id: str
    canonical_content_id: str
    metadata_fingerprint: str
    document_id: str
    source: DocumentSource
    metadata: DocumentMetadata
    generation: Generation
    pages: list[Page]
    elements: list[Element]
    # T03: structured-region outcome ledger -- one entry per detected table
    # region (successful or failed). Optional + digest-excluded (audit-only):
    # absent on pre-T03 artifacts so their content/canonical digests are
    # byte-identical; present on new candidates so the outcome is reconcilable.
    structured_outcomes: list[dict[str, Any]] = field(default_factory=list)
    # T04: formula reconciliation marker -- written by the assembler whenever a
    # formula cache was consumed (producer declares T04 semantics). Optional +
    # audit-only (participates in canonical_id, never in content identity), so
    # legacy artifacts without it keep byte-identical digests. Its presence
    # makes the formula ledger non-bypassable: formula elements on a marked
    # document MUST be reconciled against formula outcomes.
    formula_ledger: dict[str, Any] | None = None
    # T05: figure reconciliation marker -- written by the assembler on every
    # new assembly (producer declares T05 semantics). Optional + audit-only
    # (canonical_id only, never content identity), so legacy artifacts keep
    # byte-identical digests. Makes the figure ledger non-bypassable: figure
    # elements on a marked document MUST be reconciled against figure outcomes.
    figure_ledger: dict[str, Any] | None = None
    # T09: formula/explanation association marker -- written by the assembler
    # on every new assembly (producer declares T09 semantics). Optional +
    # audit-only (canonical_id only, never content identity), so legacy
    # artifacts keep byte-identical digests. Its presence makes the binding
    # ledger non-bypassable: explanation_bindings on a marked document MUST
    # reconcile (targets exist, are text elements, at most one primary per
    # explanation element) and binding_count MUST equal the recorded bindings.
    formula_context_ledger: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalDocument":
        _require_keys(data, {
            "schema_version", "canonical_id", "canonical_content_id",
            "metadata_fingerprint", "document_id", "source", "metadata",
            "generation", "pages", "elements",
        }, "document", optional={"structured_outcomes", "formula_ledger",
                                 "figure_ledger", "formula_context_ledger"})
        if data["schema_version"] != SCHEMA_VERSION:
            raise SchemaError(f"schema_version must be {SCHEMA_VERSION!r}")
        so = data.get("structured_outcomes")
        if so is not None and not isinstance(so, list):
            raise SchemaError("structured_outcomes must be a list")
        fl = data.get("formula_ledger")
        if fl is not None:
            if not isinstance(fl, dict):
                raise SchemaError("formula_ledger must be a dict")
            _require_keys(fl, {"version", "outcome_count"}, "formula_ledger")
            if fl["version"] != "t04-1":
                raise SchemaError(
                    f"formula_ledger.version must be 't04-1', got {fl['version']!r}")
            if type(fl["outcome_count"]) is not int or fl["outcome_count"] < 0:
                raise SchemaError(
                    "formula_ledger.outcome_count must be a non-negative int, "
                    f"got {fl['outcome_count']!r}")
        fgl = data.get("figure_ledger")
        if fgl is not None:
            if not isinstance(fgl, dict):
                raise SchemaError("figure_ledger must be a dict")
            _require_keys(fgl, {"version", "outcome_count"}, "figure_ledger")
            if fgl["version"] != "t05-1":
                raise SchemaError(
                    f"figure_ledger.version must be 't05-1', got {fgl['version']!r}")
            if type(fgl["outcome_count"]) is not int or fgl["outcome_count"] < 0:
                raise SchemaError(
                    "figure_ledger.outcome_count must be a non-negative int, "
                    f"got {fgl['outcome_count']!r}")
        fcl = data.get("formula_context_ledger")
        if fcl is not None:
            if not isinstance(fcl, dict):
                raise SchemaError("formula_context_ledger must be a dict")
            _require_keys(fcl, {"version", "binding_count"}, "formula_context_ledger")
            if fcl["version"] != "t09-1":
                raise SchemaError(
                    f"formula_context_ledger.version must be 't09-1', got {fcl['version']!r}")
            if type(fcl["binding_count"]) is not int or fcl["binding_count"] < 0:
                raise SchemaError(
                    "formula_context_ledger.binding_count must be a non-negative int, "
                    f"got {fcl['binding_count']!r}")
        doc = cls(
            schema_version=data["schema_version"],
            canonical_id=data["canonical_id"],
            canonical_content_id=data["canonical_content_id"],
            metadata_fingerprint=data["metadata_fingerprint"],
            document_id=data["document_id"],
            source=DocumentSource.from_dict(data["source"]),
            metadata=DocumentMetadata.from_dict(data["metadata"]),
            generation=Generation.from_dict(data["generation"]),
            pages=[Page.from_dict(p) for p in data["pages"]],
            elements=[Element.from_dict(e) for e in data["elements"]],
            structured_outcomes=list(so) if so is not None else [],
            formula_ledger=fl,
            figure_ledger=fgl,
            formula_context_ledger=fcl,
        )
        doc.validate()
        return doc

    # -- digest payloads (consumed by the V2 validator in 1.3) ---------------- #

    def _content_payload(self) -> dict[str, Any]:
        # T02 provenance/quality fields are stripped before digesting so that
        # adding them does not change canonical_content_id / canonical_id of
        # already-published artifacts (byte-identical recomputation) and two
        # candidates differing only in quality verdict share a content identity.
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "source": asdict(self.source),
            "pages": [
                {k: v for k, v in asdict(p).items() if k not in _DIGEST_EXCLUDED_PAGE_FIELDS}
                for p in self.pages
            ],
            "elements": [
                self._element_digest(e) for e in self.elements
            ],
        }

    @staticmethod
    def _element_digest(e: "Element") -> dict[str, Any]:
        """Element content-digest form: strip digest-excluded audit fields AND
        serialize content via ``to_digest_dict`` (when available) so absent
        optional content fields are omitted -- legacy artifacts keep a
        byte-identical content_id, new artifacts get content-sensitive digests.
        """
        d = {k: v for k, v in asdict(e).items() if k not in _DIGEST_EXCLUDED_ELEMENT_FIELDS}
        content = e.content
        if hasattr(content, "to_digest_dict"):
            d["content"] = content.to_digest_dict()
        else:
            d["content"] = asdict(content)
        return d

    def _full_payload(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["metadata"] = asdict(self.metadata)
        # T02 P1b (R5): the canonical audit identity includes quality decisions
        # and extraction provenance for NEW artifacts (so a quality/provenance
        # change produces a new canonical_id). It is only attached when at least
        # one audit field is present; legacy artifacts without any such field
        # yield an empty audit payload and the full payload is byte-identical to
        # the pre-T02 form -> their canonical_id is unchanged (compat).
        audit = self._audit_payload()
        # Attach the audit block only when at least one page/element actually
        # carries an audit field, or a referenced engine descriptor exists, or a
        # structured outcome is recorded. A legacy artifact produces lists of
        # empty dicts (one per page/element), no referenced engines and no
        # structured outcomes -- ``any(...)`` over those is False, so no audit
        # key is added and the payload stays byte-identical to pre-T02.
        if (any(audit["pages_audit"]) or any(audit["elements_audit"])
                or audit["engines_audit"] or audit["structured_outcomes_audit"]
                or self.formula_ledger is not None
                or self.figure_ledger is not None
                or self.formula_context_ledger is not None):
            payload["audit"] = audit
        if self.formula_ledger is not None:
            # T04: the reconciliation marker is part of the audit identity --
            # a cache/config/outcome change flips canonical_id, never content_id.
            payload["audit"]["formula_ledger"] = dict(self.formula_ledger)
        if self.figure_ledger is not None:
            # T05: same audit-identity treatment as the formula ledger.
            payload["audit"]["figure_ledger"] = dict(self.figure_ledger)
        if self.formula_context_ledger is not None:
            # T09: same audit-identity treatment -- a binding change flips
            # canonical_id, never content_id.
            payload["audit"]["formula_context_ledger"] = dict(self.formula_context_ledger)
        return payload

    def _audit_payload(self) -> dict[str, Any]:
        """Per-page/element audit fields that are actually present (non-default).

        Absent/None/empty fields are omitted so a legacy artifact (no audit
        fields) yields an empty payload -> canonical_id == pre-T02 digest. A new
        artifact with quality decisions includes them -> a quality/provenance
        change flips canonical_id while canonical_content_id stays content-only.

        ``engines_audit`` binds the actual engine descriptor (name + version)
        for every engine referenced by a page/element audit entry, so two
        candidates that both reference ``ocr-1`` but with different underlying
        versions get distinct canonical_ids (R5). It is empty for legacy
        artifacts (no page carries an engine_id).
        """
        pages_audit: list[dict[str, Any]] = []
        referenced_engine_ids: set[str] = set()
        for p in self.pages:
            entry: dict[str, Any] = {}
            if p.extraction_route is not None:
                entry["extraction_route"] = p.extraction_route
            if p.decision_status is not None:
                entry["decision_status"] = p.decision_status
            if p.decision_reasons:
                entry["decision_reasons"] = list(p.decision_reasons)
            if p.engine_id is not None:
                entry["engine_id"] = p.engine_id
                referenced_engine_ids.add(p.engine_id)
            if p.confidence is not None:
                entry["confidence"] = p.confidence
            if p.config_hash is not None:
                entry["config_hash"] = p.config_hash
            pages_audit.append(entry)
        elements_audit: list[dict[str, Any]] = []
        for e in self.elements:
            entry = {}
            if e.quality_status is not None:
                entry["quality_status"] = e.quality_status
            if e.policy_reasons:
                entry["policy_reasons"] = list(e.policy_reasons)
            if e.source_region_ids:
                entry["source_region_ids"] = list(e.source_region_ids)
            if e.processing_config_ids:
                entry["processing_config_ids"] = list(e.processing_config_ids)
            if e.quality_issues:
                entry["quality_issues"] = list(e.quality_issues)
            if e.execution_status is not None:
                entry["execution_status"] = e.execution_status
            if e.consumed_sources:
                entry["consumed_sources"] = [dict(c) for c in e.consumed_sources]
            # T04-R2: crop-asset identity and structured cross-page relations
            # participate in the canonical audit identity -- an asset or
            # relation change flips canonical_id without touching content_id.
            if e.crop_assets:
                entry["crop_assets"] = [dict(c) for c in e.crop_assets]
            if e.cross_page_relations:
                entry["cross_page_relations"] = [
                    dict(r) for r in e.cross_page_relations]
            # T09: the explanation binding list participates in the canonical
            # audit identity -- rebinding an explanation flips canonical_id.
            if e.explanation_bindings:
                entry["explanation_bindings"] = [
                    dict(b) for b in e.explanation_bindings]
            if e.ocr_fusion:
                entry["ocr_fusion"] = [dict(f) for f in e.ocr_fusion]
            elements_audit.append(entry)
        # Engine descriptors for engines actually referenced by page audit
        # entries (deterministic: in generation.engines declaration order).
        engines_audit: list[dict[str, Any]] = []
        for eng in self.generation.engines:
            if eng.engine_id in referenced_engine_ids:
                engines_audit.append({
                    "engine_id": eng.engine_id,
                    "name": eng.name,
                    "version": eng.version,
                })
        # T03 rework: FULL structured-outcome audit. Every outcome (including
        # its complete attempt ledger, selection basis, admissible result ids,
        # issues and relation-resolution issues) participates in the audit
        # identity, so an attempt / engine / config / relation / outcome change
        # flips canonical_id (R5). Sorted by region_id so the digest is stable
        # under any future change of assembly order. Empty for legacy
        # artifacts (no structured_outcomes) -- byte-identical canonical_id.
        structured_outcomes_audit: list[dict[str, Any]] = [
            dict(o) for o in sorted(
                self.structured_outcomes,
                key=lambda o: str(o.get("region_id") or ""),
            )
        ]
        return {"pages_audit": pages_audit, "elements_audit": elements_audit,
                "engines_audit": engines_audit,
                "structured_outcomes_audit": structured_outcomes_audit}

    def canonical_digest_payload(self) -> bytes:
        """Bytes fed to SHA-256 for ``canonical_id`` (audit identity)."""
        return _canonical_json(self._full_payload())

    def content_digest_payload(self) -> bytes:
        """Bytes fed to SHA-256 for ``canonical_content_id`` (cache key)."""
        return _canonical_json(self._content_payload())

    def metadata_digest_payload(self) -> bytes:
        """Bytes fed to SHA-256 for ``metadata_fingerprint``."""
        return _canonical_json(asdict(self.metadata))

    # -- invariants (design §12, 17 rules) ----------------------------------- #

    def validate(self) -> None:
        pages = self.pages
        # 4. pages.length == source.page_count, continuous and unique.
        if len(pages) != self.source.page_count:
            raise SchemaError("pages.length must equal source.page_count")
        expected = list(range(1, self.source.page_count + 1))
        if [p.physical_page for p in pages] != expected:
            raise SchemaError("physical_page must be continuous 1..page_count")
        # 2. effective_status non-unknown requires effective_status_as_of.
        if self.metadata.effective_status != "unknown" and not self.metadata.effective_status_as_of:
            raise SchemaError("effective_status_as_of required when status != unknown")
        # 1. metadata is a closed schema (enforced structurally via from_dict).

        # 5/6. element/span id uniqueness and reference resolution.
        element_ids = {e.element_id for e in self.elements}
        if len(element_ids) != len(self.elements):
            raise SchemaError("element_id values must be unique")
        span_ids: set[str] = set()
        for e in self.elements:
            for s in e.source_spans:
                if s.span_id in span_ids:
                    raise SchemaError(f"duplicate span_id {s.span_id!r}")
                span_ids.add(s.span_id)

        page_by_number = {p.physical_page: p for p in pages}
        blank_or_failed = {p.physical_page for p in pages if p.parse_status != "parsed"}

        # 16. engine resolution: every provenance engine_id AND every page-level
        # engine_id (T02 P2) must resolve into generation.engines.
        engine_ids = {eng.engine_id for eng in self.generation.engines}
        for p in pages:
            if p.engine_id is not None and p.engine_id not in engine_ids:
                raise SchemaError(
                    f"pages[{p.physical_page}].engine_id references unknown engine {p.engine_id!r}"
                )

        for e in self.elements:
            # 7. each Element has at least one primary span.
            if not any(s.role == "primary" for s in e.source_spans):
                raise SchemaError(f"element {e.element_id!r} lacks a primary span")
            for s in e.source_spans:
                # 8. bbox within page bounds.
                page = page_by_number.get(s.physical_page)
                if page is None:
                    raise SchemaError(f"span {s.span_id!r} references unknown page {s.physical_page}")
                # 9. blank/failed pages do not contribute spans.
                if s.physical_page in blank_or_failed:
                    raise SchemaError(
                        f"span {s.span_id!r} on non-parsed page {s.physical_page}"
                    )
                x0, y0, x1, y1 = s.bbox
                if not (0 <= x0 <= x1 <= page.width and 0 <= y0 <= y1 <= page.height):
                    raise SchemaError(f"span {s.span_id!r} bbox outside page bounds")
            # 11. type matches content schema (enforced by dispatch in from_dict).
            self._validate_content(e)
            # 6. source_span_ids / link targets resolve.
            for cell in getattr(e.content, "cells", []) or []:
                for sid in cell.source_span_ids:
                    if sid not in span_ids:
                        raise SchemaError(f"cell references unknown span {sid!r}")
            for note in getattr(e.content, "notes", []) or []:
                for sid in note.source_span_ids:
                    if sid not in span_ids:
                        raise SchemaError(f"note references unknown span {sid!r}")
            for ref in getattr(e.content, "references", []) or []:
                for sid in ref.source_span_ids:
                    if sid not in span_ids:
                        raise SchemaError(f"figure reference references unknown span {sid!r}")
            for link in e.links:
                if link.target_element_id not in element_ids:
                    raise SchemaError(f"link targets unknown element {link.target_element_id!r}")
            # 16 (elements). provenance engine_id resolves to generation.engines.
            for prov in e.provenance:
                if prov.engine_id not in engine_ids:
                    raise SchemaError(f"provenance references unknown engine {prov.engine_id!r}")
            # 17. T02 element quality_status, when present, must be a closed
            # vocabulary value (passed/needs_review/quarantined). Literal
            # enforcement happens in from_dict; this records the design note:
            # QA/projection fields now exist on the envelope but are
            # digest-excluded from content_id and never fabricated as ``passed``
            # without a machine rule (route + readability + region + decision).
        # 17b. page decision_status / extraction_route / engine_id closed
        # vocabulary is enforced in Page.from_dict; engine_id resolution is
        # invariant 16 (pages) above.
        # 18. T03 structured-outcome ledger validation. A ledger that is
        # present is always validated in full. A ledger that is ABSENT is only
        # tolerated on LEGACY artifacts (pre-T02 caches carry no audit fields
        # at all): a new artifact with table elements but no outcome ledger is
        # a reconciliation bypass and is rejected outright (REJECT issue 1 --
        # "旧制品兼容" must not become a gate shortcut for new artifacts).
        if self.structured_outcomes:
            self._validate_structured_outcomes()
        elif any(e.type == "table" for e in self.elements) and self._has_audit_fields():
            raise SchemaError(
                "table elements present without a structured-outcomes ledger: "
                "every detected table region must close with exactly one "
                "outcome (legacy artifacts without audit fields exempted)")
        # T04: formula reconciliation is non-bypassable ON MARKED DOCUMENTS.
        # The assembler stamps ``formula_ledger`` whenever a formula cache was
        # consumed; a marked document carrying formula elements but NO formula
        # outcomes is a reconciliation bypass. Unmarked (legacy) artifacts keep
        # validating -- their digests stay byte-identical (T02/T03 compat).
        if self.formula_ledger is not None:
            n_formula = sum(
                1 for o in self.structured_outcomes if o.get("kind") == "formula")
            if self.formula_ledger["outcome_count"] != n_formula:
                raise SchemaError(
                    f"formula_ledger.outcome_count "
                    f"{self.formula_ledger['outcome_count']!r} does not match the "
                    f"number of formula outcomes ({n_formula})")
            if n_formula == 0 and any(e.type == "formula" for e in self.elements):
                raise SchemaError(
                    "formula elements present without a formula-outcome ledger "
                    "(formula_ledger marker declares reconciliation): every "
                    "detected formula region must close with exactly one outcome")
        # T05: figure reconciliation is non-bypassable ON MARKED DOCUMENTS (the
        # assembler stamps figure_ledger on every new assembly). Unmarked
        # (legacy) artifacts keep validating -- their digests stay byte-identical.
        if self.figure_ledger is not None:
            n_figure = sum(
                1 for o in self.structured_outcomes if o.get("kind") == "figure")
            if self.figure_ledger["outcome_count"] != n_figure:
                raise SchemaError(
                    f"figure_ledger.outcome_count "
                    f"{self.figure_ledger['outcome_count']!r} does not match the "
                    f"number of figure outcomes ({n_figure})")
            if n_figure == 0 and any(e.type == "figure" for e in self.elements):
                raise SchemaError(
                    "figure elements present without a figure-outcome ledger "
                    "(figure_ledger marker declares reconciliation): every "
                    "detected figure region must close with exactly one outcome")
        # T09: formula/explanation binding reconciliation. Bindings WITHOUT the
        # producer marker are a bypass (rejected outright); a marked document
        # reconciles binding_count, target existence/type, and the PRIMARY
        # uniqueness invariant (an explanation element is primary to exactly
        # one formula). Unmarked (legacy) artifacts keep validating.
        element_by_id = {e.element_id: e for e in self.elements}
        bound_formulas = [
            e for e in self.elements if e.type == "formula" and e.explanation_bindings]
        if bound_formulas and self.formula_context_ledger is None:
            raise SchemaError(
                "explanation_bindings present without a formula_context_ledger "
                "(T09 binding semantics require the producer marker)")
        if self.formula_context_ledger is not None:
            total = sum(len(e.explanation_bindings) for e in bound_formulas)
            if self.formula_context_ledger["binding_count"] != total:
                raise SchemaError(
                    f"formula_context_ledger.binding_count "
                    f"{self.formula_context_ledger['binding_count']!r} does not "
                    f"match the number of recorded explanation bindings ({total})")
            primary_claim: dict[str, str] = {}
            for e in bound_formulas:
                if (len({b["order"] for b in e.explanation_bindings})
                        != len(e.explanation_bindings)):
                    raise SchemaError(
                        f"element {e.element_id!r}: explanation_bindings order "
                        "values must be unique within a formula")
                for b in e.explanation_bindings:
                    target = element_by_id.get(b["element_id"])
                    if target is None:
                        raise SchemaError(
                            f"element {e.element_id!r}: explanation binding "
                            f"targets unknown element {b['element_id']!r}")
                    if target.type != "text":
                        raise SchemaError(
                            f"element {e.element_id!r}: explanation binding "
                            f"target {b['element_id']!r} must be a text element, "
                            f"got type {target.type!r}")
                    if b["role"] == "primary":
                        owner = primary_claim.get(b["element_id"])
                        if owner is not None and owner != e.element_id:
                            raise SchemaError(
                                f"explanation element {b['element_id']!r} is "
                                f"primary-bound to both {owner!r} and "
                                f"{e.element_id!r} (primary binding must be unique)")
                        primary_claim[b["element_id"]] = e.element_id

    # Outcome execution vocabulary (D1 separation). ``not_detected`` is
    # deliberately absent: an outcome exists only for a DETECTED table region,
    # so "not detected" can never be a legal outcome status. ``needs_review``
    # is a QUALITY verdict, not an execution status: it is deliberately absent
    # here so "was the processing successful?" can never be conflated with
    # "does the content need review?" -- an ambiguous selection closes as
    # execution_status="failed" with quality_status="needs_review".
    _OUTCOME_EXEC_STATUSES = {"completed", "failed", "unsupported"}
    # Repo-defined table recognition routes (unified_pdf): the cropped local
    # model is primary; the others are fallbacks attempted only after the
    # primary failed. Attempt routes outside this set are rejected (REJECT
    # issue 5: closed attempt-route vocabulary).
    _OUTCOME_ATTEMPT_ROUTES = {
        "cropped_table", "full_page_fallback", "ocr_geometry_fallback",
    }
    # T04: formula recognition has exactly one repo-defined route (the formula
    # engine runs on the region crop). A table attempt must never pose as a
    # formula attempt and vice versa -- route vocabularies are kind-scoped.
    _OUTCOME_ATTEMPT_ROUTES_FORMULA = {"formula_crop_ocr"}
    # T05: figure/image processing has exactly one repo-defined route (the
    # region crop step). Kind-scoped like the formula routes above.
    _OUTCOME_ATTEMPT_ROUTES_FIGURE = {"image_crop"}
    _OUTCOME_KINDS = {"table", "formula", "figure"}
    # Attempt-level execution vocabulary (unified_pdf records only these two).
    _OUTCOME_ATTEMPT_STATUSES = {"completed", "failed"}
    _OUTCOME_KEYS = {
        "region_id", "kind", "physical_page", "bbox", "execution_status",
        "quality_status", "issues", "processing_config_id", "element_id",
        "selection_basis", "admissible_result_ids", "selected_result_id",
        "selected_attempt_id", "attempts",
    }
    # T04-R3: lossless formula-outcome provenance (optional keys so legacy
    # table outcomes and historical artifacts keep validating; REQUIRED on
    # every formula outcome of a marked document -- see validate()).
    # * detection_config_id / recognition_config_id: the page-intermediate
    #   detection config and the cache recognition profile, both verbatim;
    # * crop_assets: the REAL crop asset (ref + sha256) when the upstream
    #   recorded one -- empty + explicit ``missing_crop_asset`` issue when it
    #   did not (never a bbox posing as a crop identity, never fabricated);
    # * cross_page_relations: the structured relation records (same closed
    #   shape as the element projection) so element-less failed/unsupported
    #   outcomes remain self-sufficient for full source traceback.
    _OUTCOME_OPTIONAL_KEYS = {
        "detection_config_id", "recognition_config_id",
        "crop_assets", "cross_page_relations",
    }
    _OUTCOME_ATTEMPT_KEYS = {
        "attempt_id", "route", "execution_status", "engine_name",
        "engine_version", "config_id", "config_hash", "input_identity",
        "output_identity", "confidence", "failure_reason", "result_id",
        "selected",
    }

    def _has_audit_fields(self) -> bool:
        """True when the document carries any T02+ provenance/quality audit
        field (i.e. it is NOT a legacy artifact). Used to decide whether a
        missing structured-outcome ledger is a legacy exemption or a gate
        violation (invariant 18)."""
        for p in self.pages:
            if (p.extraction_route is not None or p.decision_status is not None
                    or p.decision_reasons or p.engine_id is not None
                    or p.confidence is not None or p.config_hash is not None):
                return True
        for e in self.elements:
            if (e.quality_status is not None or e.policy_reasons
                    or e.source_region_ids or e.processing_config_ids
                    or e.quality_issues or e.execution_status is not None
                    or e.consumed_sources or e.crop_assets
                    or e.cross_page_relations):
                return True
        return False

    def _validate_structured_outcomes(self) -> None:
        """Invariant 18: closed, reconciled structured-outcome ledger.

        Closes (REJECT issue 5): kind / execution_status / quality_status /
        physical_page / bbox / issues / admissible ids / selection_basis and
        every attempt field (route vocabulary, attempt status vocabulary,
        result binding, selected flag) are type- and value-checked here, not
        left to the digest stage.
        """
        seen_regions: set[Any] = set()
        for o in self.structured_outcomes:
            if not isinstance(o, dict):
                raise SchemaError("structured_outcomes entries must be objects")
            _require_keys(o, self._OUTCOME_KEYS, "structured_outcomes[]",
                          optional=self._OUTCOME_OPTIONAL_KEYS)
            region_id = o.get("region_id")
            if not region_id or not isinstance(region_id, str):
                raise SchemaError(
                    f"structured outcome region_id must be a non-empty string, "
                    f"got {region_id!r}")
            if region_id in seen_regions:
                raise SchemaError(
                    f"duplicate structured outcome for region {region_id!r}")
            seen_regions.add(region_id)
            if o.get("kind") not in self._OUTCOME_KINDS:
                raise SchemaError(
                    f"structured outcome {region_id!r}: kind must be one of "
                    f"{sorted(self._OUTCOME_KINDS)}, got {o.get('kind')!r}")
            status = o.get("execution_status")
            if status not in self._OUTCOME_EXEC_STATUSES:
                raise SchemaError(
                    f"structured outcome {region_id!r}: illegal execution_status "
                    f"{status!r} (not_detected is never legal for a detected region)")
            quality = o.get("quality_status")
            if quality not in get_args(QualityStatus):
                raise SchemaError(
                    f"structured outcome {region_id!r}: illegal quality_status {quality!r}")
            pno = o.get("physical_page")
            if not isinstance(pno, int) or isinstance(pno, bool) or pno < 1:
                raise SchemaError(
                    f"structured outcome {region_id!r}: physical_page must be a "
                    f"positive int, got {pno!r}")
            bbox = o.get("bbox")
            if bbox is not None:
                if (not isinstance(bbox, list) or len(bbox) != 4
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                   for v in bbox)):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: bbox must be a 4-number "
                        "list or null")
                if not (bbox[0] <= bbox[2] and bbox[1] <= bbox[3]):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: bbox must satisfy "
                        "x0<=x1 and y0<=y1")
            issues = o.get("issues")
            if (not isinstance(issues, list)
                    or not all(isinstance(i, str) for i in issues)):
                raise SchemaError(
                    f"structured outcome {region_id!r}: issues must be a list of strings")
            # T04/T05 upgrade boundary: a formula/figure outcome may only claim
            # quality "passed" when a non-empty review/assertion id is bound to
            # it. Unknown content is needs_review -- never silently upgraded.
            if (o.get("kind") in ("formula", "figure") and quality == "passed"
                    and not any(isinstance(i, str)
                                and i.startswith("review_assertion:")
                                and i[len("review_assertion:"):].strip()
                                for i in issues)):
                raise SchemaError(
                    f"structured outcome {region_id!r}: {o.get('kind')} "
                    "quality_status "
                    "'passed' requires a non-empty 'review_assertion:<id>' issue")
            # T04-R3/T05: lossless outcome provenance, when present, is
            # closed-schema validated right here (not left to the digest).
            if o.get("kind") in ("formula", "figure"):
                for cfg_key in ("detection_config_id", "recognition_config_id"):
                    v = o.get(cfg_key)
                    if v is not None and (not isinstance(v, str) or not v):
                        raise SchemaError(
                            f"structured outcome {region_id!r}: {cfg_key} must be "
                            "a non-empty string or null")
                if "crop_assets" in o:
                    _validate_crop_asset_entries(
                        o["crop_assets"],
                        f"structured outcome {region_id!r}.crop_assets")
                if "cross_page_relations" in o:
                    _validate_relation_records(
                        o["cross_page_relations"],
                        f"structured outcome {region_id!r}.cross_page_relations")
            adm = o.get("admissible_result_ids")
            if (not isinstance(adm, list)
                    or not all(isinstance(r, str) for r in adm)):
                raise SchemaError(
                    f"structured outcome {region_id!r}: admissible_result_ids must "
                    "be a list of strings")
            basis = o.get("selection_basis")
            if basis is not None and not isinstance(basis, str):
                raise SchemaError(
                    f"structured outcome {region_id!r}: selection_basis must be a "
                    "string or null")
            srid = o.get("selected_result_id")
            if srid is not None and (not isinstance(srid, str) or not srid):
                raise SchemaError(
                    f"structured outcome {region_id!r}: selected_result_id must be "
                    "a non-empty string or null")
            attempts = o.get("attempts") or []
            if not isinstance(attempts, list):
                raise SchemaError(f"structured outcome {region_id!r}: attempts must be a list")
            attempt_ids: list[str] = []
            n_selected = 0
            selected_attempt: dict[str, Any] | None = None
            for a in attempts:
                if not isinstance(a, dict):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempts entries must be objects")
                _require_keys(a, self._OUTCOME_ATTEMPT_KEYS,
                              "structured_outcomes[].attempts[]")
                aid = a.get("attempt_id")
                if not aid or not isinstance(aid, str):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt_id required")
                attempt_ids.append(aid)
                route = a.get("route")
                # Route vocabulary is KIND-SCOPED (T04/T05): table attempts use
                # the repo table routes, formula attempts use formula_crop_ocr,
                # figure attempts use image_crop.
                legal_routes = (
                    self._OUTCOME_ATTEMPT_ROUTES_FORMULA
                    if o.get("kind") == "formula"
                    else self._OUTCOME_ATTEMPT_ROUTES_FIGURE
                    if o.get("kind") == "figure"
                    else self._OUTCOME_ATTEMPT_ROUTES
                )
                if route not in legal_routes:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt {aid!r} has illegal "
                        f"route {route!r} (must be one of {sorted(legal_routes)})")
                astatus = a.get("execution_status")
                if astatus not in self._OUTCOME_ATTEMPT_STATUSES:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt {aid!r} has illegal "
                        f"execution_status {astatus!r}")
                rid = a.get("result_id")
                if rid is not None and (not isinstance(rid, str) or not rid):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt {aid!r} result_id "
                        "must be a non-empty string or null")
                if not isinstance(a.get("selected"), bool):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt.selected must be bool")
                conf = a.get("confidence")
                if conf is not None and (
                        isinstance(conf, bool) or not isinstance(conf, (int, float))
                        or not (0.0 <= float(conf) <= 1.0)):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: attempt {aid!r} confidence "
                        "must be a number in [0,1] or null")
                if a["selected"]:
                    n_selected += 1
                    selected_attempt = a
            if len(set(attempt_ids)) != len(attempt_ids):
                raise SchemaError(
                    f"structured outcome {region_id!r}: duplicate attempt_id")
            if status == "completed":
                if not o.get("element_id"):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: completed requires an element_id")
                if n_selected != 1:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: completed requires exactly "
                        "one selected attempt")
                if not o.get("selected_attempt_id"):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: completed requires "
                        "selected_attempt_id")
                if not basis:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: completed requires a "
                        "selection_basis")
                if not srid:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: completed requires a "
                        "selected_result_id")
                if srid not in adm:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: selected_result_id "
                        f"{srid!r} is not among admissible_result_ids")
                selected_ids = [a["attempt_id"] for a in attempts if a["selected"]]
                if selected_ids != [o["selected_attempt_id"]]:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: selected_attempt_id does not "
                        "match the selected attempt")
                # The selected attempt must be a COMPLETED attempt OF THE SELECTED
                # result -- a selected-but-failed attempt, or one bound to another
                # candidate, must never back a completed outcome (REJECT issue 2).
                if (selected_attempt is None
                        or selected_attempt.get("execution_status") != "completed"):
                    raise SchemaError(
                        f"structured outcome {region_id!r}: the selected attempt must "
                        "have execution_status 'completed'")
                if selected_attempt.get("result_id") != srid:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: selected attempt result_id "
                        f"{selected_attempt.get('result_id')!r} does not match "
                        f"selected_result_id {srid!r}")
            else:
                if o.get("element_id") is not None:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: a non-completed outcome "
                        "must not reference an element")
                if n_selected != 0:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: a non-completed outcome "
                        "must not mark any attempt selected")
                if o.get("selected_attempt_id") is not None:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: a non-completed outcome "
                        "must not carry a selected_attempt_id")
                if srid is not None:
                    raise SchemaError(
                        f"structured outcome {region_id!r}: a non-completed outcome "
                        "must not carry a selected_result_id")
        # Bidirectional reconciliation, kind-scoped (T03/T04/T05): each
        # completed outcome resolves to a real element of its own kind, and
        # EVERY element of that kind is claimed by exactly one completed
        # outcome. The table half is enforced whenever a ledger exists (T03
        # semantics, unchanged); the formula/figure halves are enforced
        # whenever outcomes of that kind exist OR the document declares the
        # reconciliation via ``formula_ledger`` / ``figure_ledger`` -- bare
        # formula/figure elements without outcomes stay legal only on legacy
        # artifacts.
        element_by_id = {e.element_id: e for e in self.elements}
        ledger_declared = {
            "formula": self.formula_ledger is not None,
            "figure": self.figure_ledger is not None,
        }
        for kind, etype in (("table", "table"), ("formula", "formula"),
                            ("figure", "figure")):
            kind_outcomes = [o for o in self.structured_outcomes
                             if o.get("kind") == kind]
            if (kind in ("formula", "figure") and not kind_outcomes
                    and not ledger_declared[kind]):
                continue  # legacy compat: no ledger of this kind to reconcile
            claimed = [o["element_id"] for o in kind_outcomes
                       if o.get("execution_status") == "completed"]
            if len(claimed) != len(set(claimed)):
                raise SchemaError(
                    f"a {kind} element is claimed by more than one completed "
                    "structured outcome")
            for eid in claimed:
                if eid not in element_by_id:
                    raise SchemaError(
                        f"completed outcome references unknown element {eid!r}")
            ids_of_type = {eid for eid, e in element_by_id.items()
                           if e.type == etype}
            if set(claimed) != ids_of_type:
                raise SchemaError(
                    f"{kind} elements and completed structured outcomes are out "
                    "of reconciliation")
            # T04/T05: a completed formula/figure outcome and its element must
            # carry the SAME quality verdict -- the ledger cannot be quieter or
            # louder than the element it produced.
            if kind in ("formula", "figure"):
                for o in kind_outcomes:
                    if o.get("execution_status") != "completed":
                        continue
                    el = element_by_id[o["element_id"]]
                    if el.quality_status != o.get("quality_status"):
                        raise SchemaError(
                            f"{kind} outcome {o['region_id']!r} quality_status "
                            f"{o.get('quality_status')!r} does not match element "
                            f"{o['element_id']!r} quality_status "
                            f"{el.quality_status!r}")
                    # T04-R3/T05: the SHARED provenance of a completed outcome
                    # and its element must agree -- the crop assets, the
                    # structured relations, the source region identity and the
                    # processing configs. A tampered or divergent pair is
                    # rejected here so neither side can quietly rewrite the
                    # provenance chain.
                    if "crop_assets" in o or el.crop_assets:
                        if o.get("crop_assets") != [
                                dict(c) for c in el.crop_assets]:
                            raise SchemaError(
                                f"{kind} outcome {o['region_id']!r} crop_assets "
                                f"do not match element {o['element_id']!r} "
                                "crop_assets")
                    if "cross_page_relations" in o or el.cross_page_relations:
                        if o.get("cross_page_relations") != [
                                dict(r) for r in el.cross_page_relations]:
                            raise SchemaError(
                                f"{kind} outcome {o['region_id']!r} "
                                "cross_page_relations do not match element "
                                f"{o['element_id']!r} cross_page_relations")
                    if o["region_id"] not in (el.source_region_ids or []):
                        raise SchemaError(
                            f"{kind} outcome {o['region_id']!r} does not trace "
                            f"to element {o['element_id']!r} source_region_ids "
                            f"{el.source_region_ids!r}")
                    cfgs = el.processing_config_ids or []
                    for cfg_key in ("detection_config_id",
                                    "recognition_config_id"):
                        v = o.get(cfg_key)
                        if v is not None and v not in cfgs:
                            raise SchemaError(
                                f"{kind} outcome {o['region_id']!r} {cfg_key} "
                                f"{v!r} is not among element "
                                f"{o['element_id']!r} processing_config_ids "
                                f"{cfgs!r}")
                    # the selected recognition attempt must be the one the
                    # outcome declares as the recognition config
                    rcfg = o.get("recognition_config_id")
                    if rcfg is not None and o.get("selected_attempt_id"):
                        sel = next((a for a in (o.get("attempts") or [])
                                    if a.get("attempt_id")
                                    == o["selected_attempt_id"]), None)
                        if sel is not None and sel.get("config_id") != rcfg:
                            raise SchemaError(
                                f"{kind} outcome {o['region_id']!r} selected "
                                f"attempt config_id {sel.get('config_id')!r} "
                                f"does not match recognition_config_id "
                                f"{rcfg!r}")
                # T04-R3/T05 closure: on a MARKED document every outcome of
                # this kind (completed, failed or unsupported) must carry the
                # full provenance keys -- element-less outcomes must stay
                # self-sufficient for full source traceback.
                if ledger_declared[kind]:
                    for o in kind_outcomes:
                        missing = sorted(
                            self._OUTCOME_OPTIONAL_KEYS - set(o))
                        if missing:
                            raise SchemaError(
                                f"{kind} outcome {o['region_id']!r} is missing "
                                f"lossless-provenance keys {missing} "
                                f"({kind}_ledger declares reconciliation)")

    @staticmethod
    def _validate_content(e: Element) -> None:
        # 10. no public Element.text field: structural.
        if e.type == "text":
            # 12. TextContent.text non-empty.
            if not e.content.text:
                raise SchemaError(f"element {e.element_id!r}: text content must be non-empty")
        elif e.type == "table":
            content = e.content
            # 13. grid covers all cells.
            for cell in content.cells:
                if not (0 <= cell.row < content.row_count):
                    raise SchemaError(f"element {e.element_id!r}: cell row out of range")
                if not (0 <= cell.column < content.column_count):
                    raise SchemaError(f"element {e.element_id!r}: cell column out of range")
                if cell.row + cell.row_span > content.row_count:
                    raise SchemaError(f"element {e.element_id!r}: cell row_span exceeds grid")
                if cell.column + cell.column_span > content.column_count:
                    raise SchemaError(f"element {e.element_id!r}: cell column_span exceeds grid")
        elif e.type == "formula":
            # 14. at least latex or recognized_formula.
            if not (e.content.latex or e.content.recognized_formula):
                raise SchemaError(f"element {e.element_id!r}: formula needs latex or recognized_formula")
            # T04 upgrade boundary: a formula element may only carry quality
            # "passed" when a non-empty review/assertion id is bound to it via
            # a policy reason. Unknown transcription stays needs_review.
            if e.quality_status == "passed" and not any(
                    str(r).startswith("review_assertion:")
                    and str(r)[len("review_assertion:"):].strip()
                    for r in (e.policy_reasons or [])):
                raise SchemaError(
                    f"element {e.element_id!r}: formula quality_status 'passed' "
                    "requires a 'review_assertion:<id>' policy reason")
        elif e.type == "figure":
            # 15. asset if present has full sha/media/positive pixels.
            if e.content.asset is None and not e.content.references and not e.content.caption:
                raise SchemaError(f"element {e.element_id!r}: figure has no asset/caption/references")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "canonical_id": self.canonical_id,
            "canonical_content_id": self.canonical_content_id,
            "metadata_fingerprint": self.metadata_fingerprint,
            "document_id": self.document_id,
            "source": asdict(self.source),
            "metadata": asdict(self.metadata),
            "generation": asdict(self.generation),
            "pages": [asdict(p) for p in self.pages],
            "elements": [asdict(e) for e in self.elements],
            # T03: the outcome ledger is part of the serialised form -- dropping
            # it here would make from_dict(...).to_dict(...) lossy and let a
            # round-trip bypass the reconciliation gate (REJECT issue 1).
            "structured_outcomes": [dict(o) for o in self.structured_outcomes],
            # T04: round-trip must preserve the reconciliation marker (lossless).
            "formula_ledger": dict(self.formula_ledger)
            if self.formula_ledger is not None else None,
            # T05: same lossless round-trip for the figure marker.
            "figure_ledger": dict(self.figure_ledger)
            if self.figure_ledger is not None else None,
            # T09: same lossless round-trip for the binding marker.
            "formula_context_ledger": dict(self.formula_context_ledger)
            if self.formula_context_ledger is not None else None,
        }


# --------------------------------------------------------------------------- #
# Legacy V1 compatibility shims (Page-owned; removed in Phase 3)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LegacyCanonicalElement:
    element_id: str
    type: str
    text: str
    normalized_text: str
    page_index: int
    bbox: list[float] | None
    coordinate_origin: str = "top_left"
    reading_order: int = 0
    heading_path: list[str] = field(default_factory=list)
    clause_path: list[str] = field(default_factory=list)
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    confidence: float | None = None


@dataclass(frozen=True)
class LegacyCanonicalTableCell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str
    bbox: list[float] | None = None


@dataclass(frozen=True)
class LegacyCanonicalTable:
    element_id: str
    caption: str
    html: str
    markdown: str
    cells: list[LegacyCanonicalTableCell]
    unit_context: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LegacyCanonicalPage:
    sample_id: str
    physical_page: int
    page_index: int
    display_page_label: str | None
    width: float
    height: float
    rotation: int
    extraction_route: str
    parser_name: str
    parser_profile: str
    decision_status: str
    decision_reasons: list[str]
    publishable: bool
    text: str
    elements: list[LegacyCanonicalElement]
    tables: list[LegacyCanonicalTable]
    raw_artifact_ref: str
    coordinate_normalizations: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.publishable != (self.decision_status == "approved"):
            raise ValueError("publishable must be true only for approved pages")
        if self.physical_page != self.page_index + 1:
            raise ValueError("physical_page must equal page_index + 1")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")
        for element in self.elements:
            if element.page_index != self.page_index:
                raise ValueError("element page_index must match its page")
            if element.bbox is not None:
                x0, y0, x1, y1 = element.bbox
                if not (0 <= x0 <= x1 <= self.width and 0 <= y0 <= y1 <= self.height):
                    raise ValueError(f"element bbox is outside page: {element.element_id}")


@dataclass(frozen=True)
class LegacyCanonicalDocument:
    schema_version: str
    scope: str
    asset_id: str
    document_version_id: str
    file_name: str
    sha256: str
    source_uri: str
    processing_run_id: str
    config_hash: str
    pages: list[LegacyCanonicalPage]

    def validate(self) -> None:
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("canonical document sha256 must be 64 hexadecimal characters")
        sample_ids = [page.sample_id for page in self.pages]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("canonical document sample_ids must be unique")
        for page in self.pages:
            page.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
