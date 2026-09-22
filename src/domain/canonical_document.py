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
    "table_recognition", "formula_recognition", "image_extraction",
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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class SchemaError(ValueError):
    """Raised when a dict does not conform to the V2 Canonical Schema."""


def _require_keys(data: dict[str, Any], keys: set[str], where: str) -> None:
    missing = sorted(keys - set(data))
    if missing:
        raise SchemaError(f"{where}: missing required keys {missing}")
    unknown = sorted(set(data) - keys)
    if unknown:
        raise SchemaError(f"{where}: unknown keys {unknown}")


def _check_literal(value: Any, literal: Any, where: str) -> None:
    allowed = set(get_args(literal))
    if value not in allowed:
        raise SchemaError(f"{where}: expected one of {sorted(allowed)}, got {value!r}")


def _sha256_hex(value: str, where: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise SchemaError(f"{where}: expected 64 lowercase hex, got {value!r}")


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Page":
        _require_keys(data, {
            "physical_page", "printed_label", "width", "height",
            "unit", "rotation_degrees", "parse_status", "parse_error",
        }, "pages[]")
        _check_literal(data["parse_status"], ParseStatus, "pages[].parse_status")
        if data["rotation_degrees"] not in (0, 90, 180, 270):
            raise SchemaError("pages[].rotation_degrees must be 0/90/180/270")
        if data["width"] <= 0 or data["height"] <= 0:
            raise SchemaError("pages[].width/height must be positive")
        if data["parse_status"] == "failed" and data["parse_error"] is None:
            raise SchemaError("pages[].parse_error required when parse_status=failed")
        if data["parse_status"] != "failed" and data["parse_error"] is not None:
            raise SchemaError("pages[].parse_error must be null unless parse_status=failed")
        return cls(
            physical_page=data["physical_page"],
            printed_label=data["printed_label"],
            width=data["width"],
            height=data["height"],
            unit=data["unit"],
            rotation_degrees=data["rotation_degrees"],
            parse_status=data["parse_status"],
            parse_error=ParseError.from_dict(data["parse_error"]) if data["parse_error"] else None,
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableCell":
        _require_keys(data, {
            "row", "column", "row_span", "column_span",
            "role", "text", "source_span_ids",
        }, "content.cells[]")
        _check_literal(data["role"], CellRole, "content.cells[].role")
        if data["row_span"] < 1 or data["column_span"] < 1:
            raise SchemaError("content.cells[].row_span/column_span must be >= 1")
        return cls(
            row=data["row"],
            column=data["column"],
            row_span=data["row_span"],
            column_span=data["column_span"],
            role=data["role"],
            text=data["text"],
            source_span_ids=list(data["source_span_ids"]),
        )


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableContent":
        _require_keys(data, {"row_count", "column_count", "caption", "cells", "notes"}, "content(table)")
        if data["row_count"] <= 0 or data["column_count"] <= 0:
            raise SchemaError("content.row_count/column_count must be positive")
        return cls(
            row_count=data["row_count"],
            column_count=data["column_count"],
            caption=data["caption"],
            cells=[TableCell.from_dict(c) for c in data["cells"]],
            notes=[TableNote.from_dict(n) for n in data["notes"]],
        )


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Element":
        _require_keys(data, {
            "element_id", "type", "role", "label", "section_path",
            "content", "source_spans", "provenance", "links",
        }, f"element[{data.get('element_id', '?')}]")
        etype = data["type"]
        _check_literal(etype, ElementType, "element.type")
        content_cls = _CONTENT_DISPATCH[etype]
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanonicalDocument":
        _require_keys(data, {
            "schema_version", "canonical_id", "canonical_content_id",
            "metadata_fingerprint", "document_id", "source", "metadata",
            "generation", "pages", "elements",
        }, "document")
        if data["schema_version"] != SCHEMA_VERSION:
            raise SchemaError(f"schema_version must be {SCHEMA_VERSION!r}")
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
        )
        doc.validate()
        return doc

    # -- digest payloads (consumed by the V2 validator in 1.3) ---------------- #

    def _content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "source": asdict(self.source),
            "pages": [asdict(p) for p in self.pages],
            "elements": [asdict(e) for e in self.elements],
        }

    def _full_payload(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["metadata"] = asdict(self.metadata)
        return payload

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
            # 16. provenance engine_id resolves to generation.engines.
            engine_ids = {eng.engine_id for eng in self.generation.engines}
            for prov in e.provenance:
                if prov.engine_id not in engine_ids:
                    raise SchemaError(f"provenance references unknown engine {prov.engine_id!r}")
        # 17. no QA/review/projection fields: structural (no such fields exist).

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
