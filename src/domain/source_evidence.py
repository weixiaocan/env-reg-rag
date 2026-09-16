"""Field-level provenance; a declaration is not a completed verification."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit


SOURCE_FIELDS = frozenset({
    "standard_number", "document_kind", "jurisdiction", "official_source_uri",
    "source_authority", "source_review", "publication_date", "effective_from",
    "effective_status", "local_file_match", "selection_status", "legacy_review_note",
    "expected_page_count", "completeness",
})


def require_sha256(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a lowercase SHA-256")


def require_relative_path(value: str) -> None:
    if (not isinstance(value, str) or not value or "\\" in value
            or ":" in value or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts):
        raise ValueError("evidence paths must be repository-relative POSIX paths")


def validate_reference(ref: dict[str, Any]) -> None:
    if not isinstance(ref, dict):
        raise ValueError("evidence reference must be an object")
    kind = ref.get("kind")
    if kind == "url":
        parsed = urlsplit(ref.get("url", ""))
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("expected an HTTP(S) evidence URL without credentials")
    elif kind == "file":
        require_sha256(ref.get("sha256", ""))
        pages = ref.get("physical_pages")
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError("file evidence requires positive physical pages")
    elif kind == "registry":
        require_relative_path(ref.get("path", ""))
        require_sha256(ref.get("record_sha256", ""))
        if type(ref.get("row")) is not int or ref["row"] < 2:
            raise ValueError("registry evidence requires its CSV data-row number")
    else:
        raise ValueError("unsupported evidence reference")


@dataclass(frozen=True)
class SourceObservation:
    field: str
    value: str
    origin_kind: str
    status: str
    evidence_ref: dict[str, Any]
    checked_at: str = ""
    verification_method: str = ""

    def validate(self) -> None:
        if (not all(isinstance(v, str) for v in (self.field, self.value, self.origin_kind,
                                                self.status, self.checked_at, self.verification_method))
                or not isinstance(self.evidence_ref, dict)):
            raise ValueError("source fields must be strings with an object evidence reference")
        if self.field not in SOURCE_FIELDS or not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("unsupported field or empty source value")
        if self.origin_kind not in {"legacy_record", "provider_declared", "document_extracted", "external_verified"}:
            raise ValueError("unsupported provenance origin")
        if self.status not in {"unverified", "verified"}:
            raise ValueError("unsupported observation status")
        if self.evidence_ref:
            validate_reference(self.evidence_ref)
        if self.origin_kind in {"legacy_record", "document_extracted"} and not self.evidence_ref:
            raise ValueError("this origin requires a traceable reference")
        if self.origin_kind == "legacy_record" and self.evidence_ref.get("kind") != "registry":
            raise ValueError("legacy records must identify the original registry row")
        if self.origin_kind == "document_extracted" and self.evidence_ref.get("kind") != "file":
            raise ValueError("document extraction must identify file hash and page")
        if self.origin_kind == "external_verified" and self.evidence_ref.get("kind") not in {"url", "file"}:
            raise ValueError("external evidence must identify a URL or immutable file")
        if self.field == "official_source_uri":
            validate_reference({"kind": "url", "url": self.value})
        if self.status == "verified":
            if (self.origin_kind not in {"document_extracted", "external_verified"}
                    or not self.evidence_ref or not self.verification_method.strip()):
                raise ValueError("verification requires evidence and a comparison method")
            try:
                when = datetime.fromisoformat(self.checked_at.replace("Z", "+00:00"))
            except (ValueError, TypeError) as exc:
                raise ValueError("verification requires an actual ISO check time") from exc
            if when.tzinfo is None:
                raise ValueError("verification time must include a timezone")
            if self.field in {"publication_date", "effective_from"}:
                try:
                    date.fromisoformat(self.value)
                except ValueError as exc:
                    raise ValueError("verified document dates must be ISO dates") from exc
        elif self.checked_at or self.verification_method:
            raise ValueError("unverified declarations cannot claim completed checks")
        if self.field == "expected_page_count" and (not self.value.isdecimal() or int(self.value) < 1):
            raise ValueError("expected page count must be positive")
        if self.field == "completeness":
            if self.value not in {"complete", "incomplete"}:
                raise ValueError("completeness evidence must explicitly assert complete or incomplete")
            if self.status == "verified" and self.verification_method != "full_document_comparison":
                raise ValueError("page count alone cannot verify document completeness")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, item: dict[str, Any]) -> SourceObservation:
        try:
            observation = cls(**item)
            observation.validate()
            return observation
        except (TypeError, AttributeError) as exc:
            raise ValueError("malformed source observation") from exc
