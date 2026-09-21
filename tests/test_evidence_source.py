import unittest

from src.application.evidence_source import EvidenceSourceService
from src.domain.query import EvidenceItem


class _Repository:
    def __init__(self, evidence):
        self.evidence = evidence

    def get(self, evidence_id):
        return self.evidence


class EvidenceSourceServiceTest(unittest.TestCase):
    def test_published_policy_labelled_evidence_is_returned(self):
        evidence = EvidenceItem(
            evidence_id="ev_allowed",
            text="原文证据",
            document_version_id="doc_allowed",
            file_name="规范.pdf",
            physical_pages=[1],
            heading_path=["1"],
            usage_policy="answer_and_citation",
            source_uri="https://example.org/a.pdf",
        )

        result = EvidenceSourceService(_Repository(evidence)).get("ev_allowed")

        self.assertEqual(result, evidence)

    def test_unknown_policy_or_mismatched_id_is_not_exposed(self):
        evidence = EvidenceItem(
            evidence_id="ev_other",
            text="内部中间产物",
            document_version_id="doc_other",
            file_name="中间产物.pdf",
            physical_pages=[1],
            heading_path=[],
            usage_policy="internal_only",
            source_uri="",
        )
        service = EvidenceSourceService(_Repository(evidence))

        self.assertIsNone(service.get("ev_requested"))
        self.assertIsNone(service.get("ev_other"))
        self.assertIsNone(service.get(""))


if __name__ == "__main__":
    unittest.main()
