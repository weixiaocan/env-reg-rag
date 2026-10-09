"""Formal-run gate contract: zero pending AND all-frozen AND a hash-bound
fact ledger with zero pending human rulings -- or --formal refuses."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_golden_retrieval import _fact_ledger_blockers, _formal_blockers


def _case(cid: str, *, status: str, pending: list[str] | None = None) -> dict:
    return {
        "id": cid,
        "question": f"{cid} 测试问题",
        "evidence": [
            {
                "doc_file": "a.pdf",
                "page": 1,
                "quote": "一条足够长的原文证据摘录",
                "element_ids": ["e1"],
                "must_hit": True,
                **({"pending": pending} if pending else {}),
            }
        ],
        "expected_facts": ["事实"],
        "labels": {"repr": ["定义"], "challenge": []},
        "doc_coverage": ["a.pdf"],
        "anchor_corpus": "corpus-0123456789ab",
        "expected_answerability": "answer",
        "status": status,
    }


def _ledger(facts: list[dict]) -> dict:
    return {"dataset_sha256": "", "facts": facts}


class FormalGateTests(unittest.TestCase):
    def test_all_frozen_with_zero_pending_is_formal(self) -> None:
        cases = [_case("G001", status="frozen"), _case("G002", status="frozen")]
        self.assertEqual(_formal_blockers(cases), [])

    def test_reviewed_status_blocks_formal_even_with_zero_pending(self) -> None:
        # Red light: pending is empty but the dataset is only reviewed --
        # --formal must refuse (the acceptance bar in eval/golden_v2.summary.md
        # requires every case frozen before formal metrics are reportable).
        cases = [_case("G001", status="reviewed"), _case("G002", status="frozen")]
        blockers = _formal_blockers(cases)
        self.assertTrue(blockers)
        self.assertTrue(any("frozen" in item for item in blockers))

    def test_pending_markers_still_block_formal(self) -> None:
        cases = [_case("G001", status="frozen", pending=["pdf_rendering_faithfulness"])]
        blockers = _formal_blockers(cases)
        self.assertEqual(blockers, ["G001[0]:pdf_rendering_faithfulness"])

    def test_draft_status_is_reported_per_case(self) -> None:
        cases = [_case("G001", status="draft"), _case("G002", status="reviewed")]
        blockers = _formal_blockers(cases)
        self.assertEqual(len(blockers), 2)
        self.assertIn("G001", blockers[0])
        self.assertIn("G002", blockers[1])


class FactLedgerGateTests(unittest.TestCase):
    """Round-4 rework: --formal must validate the fact ledger bound to the
    current dataset hash; pending human rulings / stale / missing all refuse."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dataset = Path(self._tmp.name) / "golden.jsonl"
        self.ledger_path = Path(self._tmp.name) / "fact_ledger.json"
        self.dataset.write_text(
            json.dumps(_case("G001", status="frozen"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self.sha = hashlib.sha256(self.dataset.read_bytes()).hexdigest()

    def _write_ledger(self, facts: list[dict], sha: str | None = None) -> Path:
        ledger = _ledger(facts)
        ledger["dataset_sha256"] = sha if sha is not None else self.sha
        self.ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        return self.ledger_path

    def test_clean_bound_ledger_allows_formal(self) -> None:
        ledger = self._write_ledger(
            [{"case": "G001", "fact_index": 0, "human_ruling": "machine_approved"}]
        )
        self.assertEqual(_fact_ledger_blockers(self.dataset, ledger), [])

    def test_pending_human_ruling_blocks_formal(self) -> None:
        ledger = self._write_ledger(
            [
                {"case": "G001", "fact_index": 0, "human_ruling": "machine_approved"},
                {"case": "G001", "fact_index": 1, "human_ruling": "pending"},
            ]
        )
        blockers = _fact_ledger_blockers(self.dataset, ledger)
        self.assertTrue(blockers)
        self.assertTrue(any("pending" in b for b in blockers), blockers)
        self.assertTrue(any("G001[f1]" in b for b in blockers), blockers)

    def test_stale_ledger_hash_blocks_formal(self) -> None:
        ledger = self._write_ledger(
            [{"case": "G001", "fact_index": 0, "human_ruling": "machine_approved"}],
            sha="0" * 64,
        )
        blockers = _fact_ledger_blockers(self.dataset, ledger)
        self.assertTrue(any("stale" in b for b in blockers), blockers)

    def test_missing_ledger_blocks_formal(self) -> None:
        blockers = _fact_ledger_blockers(self.dataset, self.ledger_path)
        self.assertTrue(any("missing" in b for b in blockers), blockers)

    def test_ledger_unreadable_blocks_formal(self) -> None:
        self.ledger_path.write_text("{not json", encoding="utf-8")
        blockers = _fact_ledger_blockers(self.dataset, self.ledger_path)
        self.assertTrue(blockers, blockers)

    def test_tampered_dataset_after_ledger_is_detected(self) -> None:
        ledger = self._write_ledger(
            [{"case": "G001", "fact_index": 0, "human_ruling": "machine_approved"}]
        )
        # mutate the dataset AFTER the ledger was bound to its hash
        rows = [json.loads(self.dataset.read_text(encoding="utf-8"))]
        rows[0]["expected_facts"][0] = "被篡改的事实"
        self.dataset.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        blockers = _fact_ledger_blockers(self.dataset, ledger)
        self.assertTrue(any("stale" in b for b in blockers), blockers)

    def test_blockers_feed_the_formal_refusal(self) -> None:
        # integration: reviewed dataset + pending ruling -> both blocker
        # sources fire, so --formal refuses even though statuses could be
        # frozen
        ledger = self._write_ledger(
            [{"case": "G001", "fact_index": 0, "human_ruling": "pending"}]
        )
        cases = [_case("G001", status="frozen")]
        combined = _formal_blockers(cases) + _fact_ledger_blockers(self.dataset, ledger)
        self.assertTrue(combined)
        self.assertTrue(any("pending" in b for b in combined), combined)


if __name__ == "__main__":
    unittest.main()

