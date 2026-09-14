import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from adapters.sqlite_corpus_registry import SQLiteCorpusRegistry
from application.corpus_lifecycle import CorpusLifecycle
from domain.corpus import AdmissionStatus, DocumentRegistration


EXPERIMENT_APPROVED_SOURCE_STATUSES = {
    "approved_experiment",
    "approved_formal",
}


@dataclass(frozen=True)
class ImportSummary:
    manifest_id: str
    selected: int
    registered: int
    admitted_for_experiment: int
    already_present: int


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def register_approved_experiment(project_root: Path, db_path: Path) -> ImportSummary:
    registry_dir = project_root / "data" / "registry"
    manifest = json.loads(
        (registry_dir / "experiment-sample-v0.json").read_text(encoding="utf-8-sig")
    )
    if manifest.get("status") != "approved_for_experiment":
        raise ValueError("experiment manifest is not approved_for_experiment")

    inventory_rows = _read_csv(registry_dir / "inventory.csv")
    review_rows = _read_csv(registry_dir / "document_reviews.csv")
    inventory_by_name = {row["file_name"]: row for row in inventory_rows}
    review_by_name = {row["file_name"]: row for row in review_rows}

    requests = []
    for selected in sorted(manifest["documents"], key=lambda item: item["order"]):
        file_name = selected["file_name"]
        inventory = inventory_by_name.get(file_name)
        review = review_by_name.get(file_name)
        if inventory is None or review is None:
            raise ValueError(f"approved sample lacks inventory or review row: {file_name}")
        expected_hash = selected["sha256"].lower()
        if inventory["sha256"].lower() != expected_hash:
            raise ValueError(f"manifest and inventory SHA-256 differ: {file_name}")
        if inventory["exact_duplicate_of"].strip():
            raise ValueError(f"approved sample is marked as an exact duplicate: {file_name}")
        if review["selection_status"] not in EXPERIMENT_APPROVED_SOURCE_STATUSES:
            raise ValueError(f"source review has not approved the sample: {file_name}")

        source_path = project_root / Path(inventory["rel_path"])
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if _sha256(source_path) != expected_hash:
            raise ValueError(f"local file no longer matches approved SHA-256: {file_name}")

        requests.append(
            DocumentRegistration(
                file_name=file_name,
                relative_path=inventory["rel_path"],
                sha256=expected_hash,
                size_bytes=source_path.stat().st_size,
                title=Path(file_name).stem,
                standard_no=inventory["std_no"] or "unknown",
                document_kind=inventory["document_kind"] or "unknown",
                jurisdiction=inventory["jurisdiction"] or "unknown",
                source_uri=review["official_source_uri"] or "unknown",
                source_review=review["source_review"] or "unknown",
                effective_status=review["effective_status"] or "unknown",
            )
        )

    # Validate the complete manifest before the first database write so a bad
    # later item cannot leave a partially imported experiment selection.
    for request in requests:
        request.validate()

    lifecycle = CorpusLifecycle(SQLiteCorpusRegistry(db_path))
    existing_hashes = {d.registration.sha256 for d in lifecycle.list_documents()}
    registered = 0
    admitted = 0
    already_present = 0

    for request in requests:
        file_name = request.file_name
        expected_hash = request.sha256
        document = lifecycle.register(request)
        if expected_hash in existing_hashes:
            already_present += 1
        else:
            registered += 1
            existing_hashes.add(expected_hash)

        if document.admission_status == AdmissionStatus.NEEDS_REVIEW:
            lifecycle.change_admission(
                document.document_version_id,
                AdmissionStatus.EXPERIMENT_ONLY,
                reason=manifest["approval_scope"],
                actor_role="project_maintainer",
            )
            admitted += 1
        elif document.admission_status != AdmissionStatus.EXPERIMENT_ONLY:
            raise ValueError(
                f"approved sample has incompatible admission state: {file_name} "
                f"({document.admission_status.value})"
            )

    return ImportSummary(
        manifest_id=manifest["manifest_id"],
        selected=len(manifest["documents"]),
        registered=registered,
        admitted_for_experiment=admitted,
        already_present=already_present,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the approved M2 experiment corpus")
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "data" / "registry" / "corpus_registry.sqlite3",
    )
    args = parser.parse_args()
    summary = register_approved_experiment(PROJECT_ROOT, args.db)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
