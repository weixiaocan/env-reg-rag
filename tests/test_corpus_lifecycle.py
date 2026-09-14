import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.corpus_lifecycle import CorpusLifecycle
from adapters.sqlite_corpus_registry import SQLiteCorpusRegistry
from domain.corpus import (
    AdmissionStatus,
    DocumentRegistration,
    InvalidAdmissionTransition,
    RegistrationConflict,
)
from scripts.register_experiment_corpus import register_approved_experiment


class CorpusLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "registry.sqlite3"
        self.registry = SQLiteCorpusRegistry(db_path)
        self.lifecycle = CorpusLifecycle(self.registry)
        self.request = DocumentRegistration(
            file_name="rainfall.pdf",
            relative_path="data/raw/rainfall.pdf",
            sha256="a" * 64,
            size_bytes=128,
            title="降水量等级",
            standard_no="GB/T 28592-2012",
            document_kind="standard",
            jurisdiction="全国",
            source_uri="https://example.test/rainfall",
            source_review="official_record_found",
            effective_status="current",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_registration_is_idempotent_for_same_asset_and_metadata(self):
        first = self.lifecycle.register(self.request)
        second = self.lifecycle.register(self.request)

        self.assertEqual(first.document_version_id, second.document_version_id)
        self.assertEqual(AdmissionStatus.NEEDS_REVIEW, second.admission_status)
        self.assertEqual(1, len(self.lifecycle.list_documents()))
        self.assertEqual(1, len(self.lifecycle.admission_history(first.document_version_id)))

    def test_same_hash_with_conflicting_identity_is_rejected(self):
        self.lifecycle.register(self.request)
        conflicting = DocumentRegistration(
            **{**self.request.__dict__, "standard_no": "GB/T 99999-2099"}
        )

        with self.assertRaises(RegistrationConflict):
            self.lifecycle.register(conflicting)

    def test_same_hash_with_changed_source_review_is_not_silently_ignored(self):
        self.lifecycle.register(self.request)
        changed_review = DocumentRegistration(
            **{**self.request.__dict__, "source_review": "official_fulltext_found"}
        )

        with self.assertRaises(RegistrationConflict):
            self.lifecycle.register(changed_review)

    def test_database_schema_is_migrated_to_version_one_and_reopens(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "migration.sqlite3"
            first = SQLiteCorpusRegistry(db_path)
            self.assertEqual(1, first.schema_version)
            CorpusLifecycle(first).register(self.request)

            reopened = SQLiteCorpusRegistry(db_path)
            self.assertEqual(1, reopened.schema_version)
            self.assertEqual(1, len(CorpusLifecycle(reopened).list_documents()))

    def test_stale_admission_write_is_rejected_by_repository_transaction(self):
        document = self.lifecycle.register(self.request)
        self.registry.change_admission(
            document.document_version_id,
            AdmissionStatus.QUARANTINED,
            expected_current=AdmissionStatus.NEEDS_REVIEW,
            reason="暂时隔离",
            actor_role="project_maintainer",
        )

        with self.assertRaises(InvalidAdmissionTransition):
            self.registry.change_admission(
                document.document_version_id,
                AdmissionStatus.FORMAL_ELIGIBLE,
                expected_current=AdmissionStatus.NEEDS_REVIEW,
                reason="基于过期读取写入",
                actor_role="project_maintainer",
            )

    def test_approved_sample_can_move_to_experiment_only_with_audit_history(self):
        document = self.lifecycle.register(self.request)
        changed = self.lifecycle.change_admission(
            document.document_version_id,
            AdmissionStatus.EXPERIMENT_ONLY,
            reason="批准用于 M2 解析实验",
            actor_role="project_maintainer",
        )

        self.assertEqual(AdmissionStatus.EXPERIMENT_ONLY, changed.admission_status)
        history = self.lifecycle.admission_history(document.document_version_id)
        self.assertEqual(2, len(history))
        self.assertEqual(AdmissionStatus.NEEDS_REVIEW, history[-1].from_status)
        self.assertEqual(AdmissionStatus.EXPERIMENT_ONLY, history[-1].to_status)
        self.assertEqual("批准用于 M2 解析实验", history[-1].reason)
        self.assertEqual("project_maintainer", history[-1].actor_role)

    def test_invalid_admission_transition_does_not_change_state(self):
        document = self.lifecycle.register(self.request)
        self.lifecycle.change_admission(
            document.document_version_id,
            AdmissionStatus.EXPERIMENT_ONLY,
            reason="批准实验",
            actor_role="project_maintainer",
        )

        with self.assertRaises(InvalidAdmissionTransition):
            self.lifecycle.change_admission(
                document.document_version_id,
                AdmissionStatus.NEEDS_REVIEW,
                reason="错误回退",
                actor_role="project_maintainer",
            )

        current = self.lifecycle.get_document(document.document_version_id)
        self.assertEqual(AdmissionStatus.EXPERIMENT_ONLY, current.admission_status)
        self.assertEqual(2, len(self.lifecycle.admission_history(document.document_version_id)))


class ApprovedExperimentImportTest(unittest.TestCase):
    def test_current_approved_manifest_imports_eight_documents_idempotently(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "registry.sqlite3"

            first = register_approved_experiment(project_root, db_path)
            second = register_approved_experiment(project_root, db_path)
            lifecycle = CorpusLifecycle(SQLiteCorpusRegistry(db_path))

            self.assertEqual(8, first.registered)
            self.assertEqual(8, first.admitted_for_experiment)
            self.assertEqual(0, second.registered)
            self.assertEqual(0, second.admitted_for_experiment)
            documents = lifecycle.list_documents()
            self.assertEqual(8, len(documents))
            self.assertTrue(
                all(d.admission_status == AdmissionStatus.EXPERIMENT_ONLY for d in documents)
            )
            self.assertTrue(
                all(len(lifecycle.admission_history(d.document_version_id)) == 2 for d in documents)
            )


if __name__ == "__main__":
    unittest.main()
