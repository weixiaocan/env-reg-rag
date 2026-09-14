from domain.corpus import (
    AdmissionEvent,
    AdmissionStatus,
    DocumentNotFound,
    DocumentRecord,
    DocumentRegistration,
    RegistrationConflict,
    ensure_admission_transition,
)


class CorpusLifecycle:
    """Stable application boundary for corpus-control operations."""

    def __init__(self, registry):
        self._registry = registry

    def register(self, request: DocumentRegistration) -> DocumentRecord:
        request.validate()
        existing = self._registry.get_by_sha256(request.sha256.lower())
        if existing is not None:
            if existing.registration.idempotency_key() != request.idempotency_key():
                raise RegistrationConflict(
                    "the SHA-256 is already registered with different metadata"
                )
            return existing
        return self._registry.register(request)

    def get_document(self, document_version_id: str) -> DocumentRecord:
        document = self._registry.get(document_version_id)
        if document is None:
            raise DocumentNotFound(document_version_id)
        return document

    def list_documents(self) -> list[DocumentRecord]:
        return self._registry.list_documents()

    def change_admission(
        self,
        document_version_id: str,
        target: AdmissionStatus,
        *,
        reason: str,
        actor_role: str,
    ) -> DocumentRecord:
        if not reason.strip():
            raise ValueError("an admission change requires a reason")
        if not actor_role.strip():
            raise ValueError("an admission change requires an actor role")
        current = self.get_document(document_version_id)
        ensure_admission_transition(current.admission_status, target)
        return self._registry.change_admission(
            document_version_id,
            target,
            expected_current=current.admission_status,
            reason=reason,
            actor_role=actor_role,
        )

    def admission_history(self, document_version_id: str) -> list[AdmissionEvent]:
        self.get_document(document_version_id)
        return self._registry.admission_history(document_version_id)
