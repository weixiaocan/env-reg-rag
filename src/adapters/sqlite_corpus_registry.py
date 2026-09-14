import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain.corpus import (
    AdmissionEvent,
    AdmissionStatus,
    DocumentRecord,
    DocumentRegistration,
    InvalidAdmissionTransition,
    ensure_admission_transition,
)


SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_versions (
    document_version_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL UNIQUE REFERENCES assets(asset_id),
    title TEXT NOT NULL,
    standard_no TEXT NOT NULL,
    document_kind TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_review TEXT NOT NULL,
    effective_status TEXT NOT NULL,
    admission_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admission_events (
    event_id TEXT PRIMARY KEY,
    document_version_id TEXT NOT NULL REFERENCES document_versions(document_version_id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admission_events_document
ON admission_events(document_version_id, occurred_at, event_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteCorpusRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @property
    def schema_version(self) -> int:
        with self._session() as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        with self._session() as connection:
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            if current < 1:
                connection.executescript(SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, request: DocumentRegistration) -> DocumentRecord:
        sha256 = request.sha256.lower()
        asset_id = f"asset_{sha256}"
        document_version_id = f"doc_{sha256}"
        occurred_at = _now()
        with self._session() as connection:
            connection.execute(
                """INSERT INTO assets
                   (asset_id, sha256, file_name, relative_path, size_bytes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    sha256,
                    request.file_name,
                    request.relative_path,
                    request.size_bytes,
                    occurred_at,
                ),
            )
            connection.execute(
                """INSERT INTO document_versions
                   (document_version_id, asset_id, title, standard_no, document_kind,
                    jurisdiction, source_uri, source_review, effective_status,
                    admission_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    document_version_id,
                    asset_id,
                    request.title,
                    request.standard_no,
                    request.document_kind,
                    request.jurisdiction,
                    request.source_uri,
                    request.source_review,
                    request.effective_status,
                    AdmissionStatus.NEEDS_REVIEW.value,
                    occurred_at,
                    occurred_at,
                ),
            )
            connection.execute(
                """INSERT INTO admission_events
                   (event_id, document_version_id, from_status, to_status, reason,
                    actor_role, occurred_at)
                   VALUES (?, ?, NULL, ?, ?, ?, ?)""",
                (
                    f"evt_{uuid4().hex}",
                    document_version_id,
                    AdmissionStatus.NEEDS_REVIEW.value,
                    "initial registration",
                    "system",
                    occurred_at,
                ),
            )
        document = self.get(document_version_id)
        assert document is not None
        return document

    def get_by_sha256(self, sha256: str) -> DocumentRecord | None:
        return self._fetch_one("WHERE a.sha256 = ?", (sha256.lower(),))

    def get(self, document_version_id: str) -> DocumentRecord | None:
        return self._fetch_one("WHERE d.document_version_id = ?", (document_version_id,))

    def list_documents(self) -> list[DocumentRecord]:
        with self._session() as connection:
            query = self._select_sql() + " ORDER BY d.created_at, d.document_version_id"
            rows = connection.execute(query).fetchall()
        return [self._to_document(row) for row in rows]

    def change_admission(
        self,
        document_version_id: str,
        target: AdmissionStatus,
        *,
        expected_current: AdmissionStatus,
        reason: str,
        actor_role: str,
    ) -> DocumentRecord:
        occurred_at = _now()
        with self._session() as connection:
            row = connection.execute(
                "SELECT admission_status FROM document_versions WHERE document_version_id = ?",
                (document_version_id,),
            ).fetchone()
            current = AdmissionStatus(row["admission_status"])
            if current != expected_current:
                raise InvalidAdmissionTransition(
                    "admission state changed while the request was being processed"
                )
            ensure_admission_transition(current, target)
            result = connection.execute(
                """UPDATE document_versions
                   SET admission_status = ?, updated_at = ?
                   WHERE document_version_id = ? AND admission_status = ?""",
                (target.value, occurred_at, document_version_id, current.value),
            )
            if result.rowcount != 1:
                raise InvalidAdmissionTransition(
                    "admission state changed while the request was being processed"
                )
            connection.execute(
                """INSERT INTO admission_events
                   (event_id, document_version_id, from_status, to_status, reason,
                    actor_role, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"evt_{uuid4().hex}",
                    document_version_id,
                    current.value,
                    target.value,
                    reason,
                    actor_role,
                    occurred_at,
                ),
            )
        document = self.get(document_version_id)
        assert document is not None
        return document

    def admission_history(self, document_version_id: str) -> list[AdmissionEvent]:
        with self._session() as connection:
            rows = connection.execute(
                """SELECT document_version_id, from_status, to_status, reason,
                          actor_role, occurred_at
                   FROM admission_events
                   WHERE document_version_id = ?
                   ORDER BY occurred_at, event_id""",
                (document_version_id,),
            ).fetchall()
        return [
            AdmissionEvent(
                document_version_id=row["document_version_id"],
                from_status=(AdmissionStatus(row["from_status"]) if row["from_status"] else None),
                to_status=AdmissionStatus(row["to_status"]),
                reason=row["reason"],
                actor_role=row["actor_role"],
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

    def _fetch_one(self, where: str, parameters: tuple[str, ...]) -> DocumentRecord | None:
        with self._session() as connection:
            row = connection.execute(self._select_sql() + " " + where, parameters).fetchone()
        return self._to_document(row) if row else None

    @staticmethod
    def _select_sql() -> str:
        return """SELECT d.*, a.sha256, a.file_name, a.relative_path, a.size_bytes
                  FROM document_versions d
                  JOIN assets a ON a.asset_id = d.asset_id"""

    @staticmethod
    def _to_document(row: sqlite3.Row) -> DocumentRecord:
        registration = DocumentRegistration(
            file_name=row["file_name"],
            relative_path=row["relative_path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            title=row["title"],
            standard_no=row["standard_no"],
            document_kind=row["document_kind"],
            jurisdiction=row["jurisdiction"],
            source_uri=row["source_uri"],
            source_review=row["source_review"],
            effective_status=row["effective_status"],
        )
        return DocumentRecord(
            document_version_id=row["document_version_id"],
            asset_id=row["asset_id"],
            registration=registration,
            admission_status=AdmissionStatus(row["admission_status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
