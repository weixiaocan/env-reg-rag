from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class AdmissionStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    EXPERIMENT_ONLY = "experiment_only"
    FORMAL_ELIGIBLE = "formal_eligible"
    QUARANTINED = "quarantined"
    WITHDRAWN = "withdrawn"


class ProcessingStatus(StrEnum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    QUALITY_PASSED = "quality_passed"
    FAILED = "failed"


class CorpusReleaseStatus(StrEnum):
    DRAFT = "draft"
    BUILT = "built"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


ALLOWED_ADMISSION_TRANSITIONS = {
    AdmissionStatus.NEEDS_REVIEW: {
        AdmissionStatus.EXPERIMENT_ONLY,
        AdmissionStatus.FORMAL_ELIGIBLE,
        AdmissionStatus.QUARANTINED,
    },
    AdmissionStatus.EXPERIMENT_ONLY: {
        AdmissionStatus.FORMAL_ELIGIBLE,
        AdmissionStatus.QUARANTINED,
        AdmissionStatus.WITHDRAWN,
    },
    AdmissionStatus.FORMAL_ELIGIBLE: {
        AdmissionStatus.QUARANTINED,
        AdmissionStatus.WITHDRAWN,
    },
    AdmissionStatus.QUARANTINED: {AdmissionStatus.NEEDS_REVIEW},
    AdmissionStatus.WITHDRAWN: set(),
}


class CorpusLifecycleError(Exception):
    """Base error for corpus lifecycle operations."""


class RegistrationConflict(CorpusLifecycleError):
    """The same immutable asset was registered with conflicting identity data."""


class InvalidAdmissionTransition(CorpusLifecycleError):
    """A requested document admission transition violates the domain rules."""


class DocumentNotFound(CorpusLifecycleError):
    """The requested document version is not registered."""


@dataclass(frozen=True)
class DocumentRegistration:
    file_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    title: str
    standard_no: str
    document_kind: str
    jurisdiction: str
    source_uri: str
    source_review: str
    effective_status: str

    def validate(self) -> None:
        normalized = self.sha256.lower()
        if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
            raise ValueError("sha256 must contain exactly 64 hexadecimal characters")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")

    def idempotency_key(self) -> tuple:
        """All persisted inputs must match before registration can be a no-op."""
        return (
            self.file_name,
            self.relative_path,
            self.sha256.lower(),
            self.size_bytes,
            self.title,
            self.standard_no,
            self.document_kind,
            self.jurisdiction,
            self.source_uri,
            self.source_review,
            self.effective_status,
        )


@dataclass(frozen=True)
class DocumentRecord:
    document_version_id: str
    asset_id: str
    registration: DocumentRegistration
    admission_status: AdmissionStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AdmissionEvent:
    document_version_id: str
    from_status: Optional[AdmissionStatus]
    to_status: AdmissionStatus
    reason: str
    actor_role: str
    occurred_at: str


def ensure_admission_transition(
    current: AdmissionStatus, target: AdmissionStatus
) -> None:
    if target not in ALLOWED_ADMISSION_TRANSITIONS[current]:
        raise InvalidAdmissionTransition(
            f"admission cannot change from {current.value} to {target.value}"
        )
