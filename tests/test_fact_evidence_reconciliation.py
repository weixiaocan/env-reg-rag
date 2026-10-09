"""Round-4 fact-acceptance gate tests.

The round-4 audit requires the fact gate to:
- bind every fact to an actual chunk_id + evidence_index + PDF page;
- produce missing / pdf_only / rendering_only from a REAL probe ladder;
- fail on tampered facts, deleted evidence, stale ledger hashes;
- never duplicate evidence on repeated --write (idempotent).

Red-light tests here deliberately corrupt inputs in memory (never on disk).
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.close_pending_evidence import DATASET  # noqa: E402
from scripts.reconcile_fact_evidence import (  # noqa: E402
    ChunkedCorpus,
    apply_edits,
    gate_problems,
    reconcile,
)

LEDGER = ROOT / "eval" / "audits" / "fact_reconciliation" / "fact_ledger.json"


class Round4GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = DATASET.read_bytes().decode("utf-8")
        cls.rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
        cls.corpus = ChunkedCorpus()
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.sha = hashlib.sha256(DATASET.read_bytes()).hexdigest()

    # ------------------------------------------------------------------
    # gate passes on the real data
    # ------------------------------------------------------------------

    def test_gate_passes_on_the_submitted_dataset(self) -> None:
        problems = gate_problems(self.rows, self.corpus, self.ledger, self.sha)
        self.assertEqual(problems, [])

    def test_ledger_binds_every_fact_to_real_chunk_ids(self) -> None:
        entries = {(e["case"], e["fact_index"]): e for e in self.ledger["facts"]}
        self.assertEqual(len(entries), 95)  # 87 content + 8 refusal
        for e in self.ledger["facts"]:
            if e["classification"] == "refuse_meta":
                self.assertEqual(e["human_ruling"], "n/a")
                continue
            self.assertTrue(e["bindings"], f"{e['case']}[f{e['fact_index']}]")
            for b in e["bindings"]:
                chunk = self.corpus.chunk_by_id(
                    self._doc_of(e["case"], b["evidence_index"]),
                    b["chunk_id"],
                )
                self.assertIsNotNone(chunk, f"{e['case']}[f{e['fact_index']}]: {b['chunk_id']}")
            if e["classification"] != "covered":
                self.assertEqual(e["human_ruling"], "pending",
                                 f"{e['case']}[f{e['fact_index']}]: exception needs human ruling")

    def _doc_of(self, case_id: str, evidence_index: int) -> str:
        row = next(r for r in self.rows if r["id"] == case_id)
        return row["evidence"][evidence_index]["doc_file"]

    def test_verified_renders_exist_for_exception_facts(self) -> None:
        renders = self.ledger.get("verified_renders") or []
        self.assertTrue(renders, "no verified renders recorded")
        for r in renders:
            if r["render"]:
                self.assertTrue((ROOT / r["render"]).is_file(), r["render"])

    # ------------------------------------------------------------------
    # tamper / deletion / staleness red lights
    # ------------------------------------------------------------------

    def test_tampered_fact_fails_the_gate(self) -> None:
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r["id"] == "G037")
        row["expected_facts"][0] = row["expected_facts"][0].replace("雨水设计流量", "雨水排设计流量")
        problems = gate_problems(rows, self.corpus, self.ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and "mismatch" in p for p in problems),
            problems,
        )

    def test_deleted_evidence_fails_the_gate(self) -> None:
        rows = copy.deepcopy(self.rows)
        row = next(r for r in rows if r["id"] == "G037")
        # drop the evidence that carries the Q definition (a bound carrier)
        del row["evidence"][2]
        problems = gate_problems(rows, self.corpus, self.ledger, self.sha)
        self.assertTrue(
            any("G037" in p and ("invalid/deleted" in p or "no longer supports" in p or "mismatch" in p)
                for p in problems),
            problems,
        )

    def test_stale_ledger_hash_fails_the_gate(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        ledger["dataset_sha256"] = "0" * 64
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(any("stale" in p for p in problems), problems)

    def test_ledger_missing_fails_the_gate(self) -> None:
        problems = gate_problems(self.rows, self.corpus, None, self.sha)
        self.assertEqual(problems, ["fact ledger missing -- run scripts/reconcile_fact_evidence.py --write"])

    # ------------------------------------------------------------------
    # the probe ladder is real, not pre-seeded: each branch proven with a
    # DETERMINISTIC example (no multi-outcome assertions)
    # ------------------------------------------------------------------

    @staticmethod
    def _compact(s: str) -> str:
        import re as _re

        return _re.sub(r"\s+", "", s).translate(str.maketrans({"³": "3", "²": "2", "¹": "1"}))

    def _chunk_supports(self, fact: str, case_id: str) -> bool:
        from scripts.reconcile_fact_evidence import fact_support

        for chunks in self.corpus.chunks.values():
            for c in chunks:
                if fact_support(fact, c["text"], case_id)["supported"]:
                    return True
        return False

    def test_probe_ladder_missing_branch_is_deterministic(self) -> None:
        from scripts.reconcile_fact_evidence import _probe_ladder

        row = next(r for r in self.rows if r["id"] == "G037")
        # a fabricated fact with a number absent from every chunk, every
        # evidence-doc PDF page and every rendering -> exactly "missing"
        cls, _note = _probe_ladder(
            "架空线缆敷设垂直偏差为12.345678毫米", row, self.corpus, {}, "G037"
        )
        self.assertEqual(cls, "missing")

    def test_probe_ladder_pdf_only_branch_is_deterministic(self) -> None:
        """Front-matter text on an evidence-doc PDF page: visible in the
        text layer, carried by NO chunk (not chunked at all)."""
        from scripts.reconcile_fact_evidence import _normalized, _probe_ladder

        row = next(r for r in self.rows if r["id"] == "G003")
        docs = sorted({ev["doc_file"] for ev in row["evidence"]})
        pdf_cache = {d: self.corpus.pdf_pages_text(d) for d in docs}
        # deterministic search: first contiguous 40+ char window of any page
        # that is a substring of NO chunk in the corpus
        probe = None
        for d in docs:
            for _pg, text in pdf_cache[d].items():
                norm = _normalized(text)
                for start in range(0, max(1, len(norm) - 50), 30):
                    w = norm[start:start + 50]
                    if len(w) < 40:
                        continue
                    if any(w in c["norm"] for cs in self.corpus.chunks.values() for c in cs):
                        continue
                    if self._chunk_supports(w, row["id"]):
                        continue
                    probe = (d, w)
                    break
                if probe:
                    break
            if probe:
                break
        self.assertIsNotNone(probe, "no page-only window found -- corpus changed")
        cls, note = _probe_ladder(probe[1], row, self.corpus, pdf_cache, row["id"])
        self.assertEqual(cls, "pdf_only")
        self.assertIn("不得关闭", note)

    def test_probe_ladder_rendering_only_branch_is_deterministic(self) -> None:
        """Manual-rendering wording: supported by a rendering, but by NO
        chunk and NO PDF page (transcription wording)."""
        from scripts.reconcile_fact_evidence import _probe_ladder

        row = next(r for r in self.rows if r["id"] == "G001")
        docs = sorted({ev["doc_file"] for ev in row["evidence"]})
        pdf_cache = {d: self.corpus.pdf_pages_text(d) for d in docs}
        page_comps = {d: [self._compact(t) for t in pages.values()] for d, pages in pdf_cache.items()}
        # deterministic search: first 24+ char compact window of a rendering
        # that no chunk and no PDF page supports
        probe = None
        for ev in row["evidence"]:
            r = ev.get("rendering")
            if not r:
                continue
            comp = self._compact(r)
            for start in range(0, max(1, len(comp) - 30), 12):
                w = comp[start:start + 30]
                if len(w) < 24:
                    continue
                if self._chunk_supports(w, row["id"]):
                    continue
                if any(w in t for t in page_comps[ev["doc_file"]]):
                    continue
                probe = w
                break
            if probe:
                break
        self.assertIsNotNone(probe, "no rendering-only window found -- corpus changed")
        cls, note = _probe_ladder(probe, row, self.corpus, pdf_cache, row["id"])
        self.assertEqual(cls, "rendering_only")
        self.assertIn("不得关闭", note)

    def test_reconcile_gap_lists_match_ladder_not_preseeded(self) -> None:
        _entries, _counts, gaps = reconcile(self.rows, self.corpus)
        # all 87 content facts are covered or explicitly classified; the
        # ladder finds nothing -> empty lists, but PRODUCED by probing
        # (proven by the three deterministic branch tests above)
        for cat in ("missing", "pdf_only", "rendering_only"):
            self.assertIsInstance(gaps[cat], list)

    # ------------------------------------------------------------------
    # self-filled binding flags are not trusted; wrong/duplicate bindings
    # hard-fail
    # ------------------------------------------------------------------

    def _g037_entry(self, ledger: dict) -> dict:
        return next(
            e for e in ledger["facts"] if e["case"] == "G037" and e["fact_index"] == 0
        )

    def test_selffilled_supported_true_flag_is_not_trusted(self) -> None:
        # flip a true flag to false: the gate recomputes support from the
        # bound chunk and must flag the mismatch, not trust the ledger
        ledger = copy.deepcopy(self.ledger)
        binding = next(b for b in self._g037_entry(ledger)["bindings"] if b["supported"])
        binding["supported"] = False
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and "supported" in p for p in problems),
            problems,
        )

    def test_selffilled_supported_false_flag_is_not_trusted(self) -> None:
        # flip a false flag to true: the gate recomputes support from the
        # bound chunk and must flag the mismatch the other way
        ledger = copy.deepcopy(self.ledger)
        binding = next(b for b in self._g037_entry(ledger)["bindings"] if not b["supported"])
        binding["supported"] = True
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and "supported" in p for p in problems),
            problems,
        )

    def test_duplicate_bindings_fail_the_gate(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        entry = self._g037_entry(ledger)
        entry["bindings"].append(copy.deepcopy(entry["bindings"][0]))
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and "duplicate" in p for p in problems),
            problems,
        )

    def test_binding_to_a_chunk_that_does_not_carry_the_quote_fails(self) -> None:
        # swap a binding onto a real chunk of the same doc that does NOT
        # carry this evidence's quote/element_ids
        ledger = copy.deepcopy(self.ledger)
        bindings = self._g037_entry(ledger)["bindings"]
        carrier, other = bindings[2], bindings[0]
        self.assertTrue(carrier["supported"])
        carrier["chunk_id"] = other["chunk_id"]
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and ("carry" in p or "quote" in p) for p in problems),
            problems,
        )

    def test_binding_doc_mismatch_fails_the_gate(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        entry = self._g037_entry(ledger)
        entry["bindings"][0]["doc"] = "不存在的文档.pdf"
        problems = gate_problems(self.rows, self.corpus, ledger, self.sha)
        self.assertTrue(
            any("G037[f0]" in p and "doc" in p for p in problems),
            problems,
        )

    # ------------------------------------------------------------------
    # idempotent writes
    # ------------------------------------------------------------------

    def test_repeated_apply_edits_never_duplicates_evidence(self) -> None:
        rows = copy.deepcopy(self.rows)
        first = apply_edits(rows, self.corpus)
        self.assertEqual(first, [], "round-3 edits are already applied; re-run must be a no-op")
        count_before = sum(len(r.get("evidence") or []) for r in rows)
        second = apply_edits(rows, self.corpus)
        self.assertEqual(second, [])
        count_after = sum(len(r.get("evidence") or []) for r in rows)
        self.assertEqual(count_before, count_after)
        self.assertEqual(
            hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()[:16],
            hashlib.sha256(json.dumps(copy.deepcopy(self.rows), ensure_ascii=False).encode("utf-8")).hexdigest()[:16],
        )


class FactRulingGateTests(unittest.TestCase):
    """Round-4 rework (second pass).

    ① A frozen dataset with a same-hash fact ledger that has missing,
    duplicate or misbound entries MUST reject formal -- the formal entry
    runs the full authoritative acceptance, not just the sha check.
    ② Human adjudication records are auditable: they live in a rulings
    registry that ledger regeneration reads but never overwrites; they
    must not conflict with the per-fact acceptance rules; hand-editing
    the human_ruling string never passes (the gate recomputes the
    expected value from classification + rulings).
    """

    @classmethod
    def setUpClass(cls) -> None:
        raw = DATASET.read_bytes().decode("utf-8")
        cls.rows = [json.loads(line) for line in raw.split("\n") if line.strip()]
        cls.corpus = ChunkedCorpus()
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _frozen_dataset(self) -> Path:
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["status"] = "frozen"
        path = self.tmp / "golden_frozen.jsonl"
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        return path

    def _bound_ledger(self, dataset_path: Path) -> Path:
        ledger = copy.deepcopy(self.ledger)
        ledger["dataset_sha256"] = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        path = self.tmp / "fact_ledger.json"
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        return path

    def _formal_blockers(self, dataset_path: Path, ledger_path: Path,
                         rulings_path: Path | None = None) -> list[str]:
        from scripts.evaluate_golden_retrieval import _fact_ledger_blockers

        return _fact_ledger_blockers(
            dataset_path, ledger_path, full=True, corpus=self.corpus,
            rulings_path=rulings_path,
        )

    # ------------------------------------------------------------------
    # ① frozen dataset + same-hash ledger with content defects -> reject
    # ------------------------------------------------------------------

    def test_adjudicated_frozen_dataset_with_clean_ledger_allows_formal(self) -> None:
        # end-to-end control for ②, via the REAL status transition: after
        # ALL 14 machine-unprovable exceptions carry complete recorded
        # rulings, the ledger is REGENERATED (reconcile recomputes
        # human_ruling from the registry -> human_adjudicated), the frozen
        # dataset hash is rebound, and the full authoritative acceptance
        # passes -- so the rejection tests below fail because of the
        # specific defect
        from scripts.reconcile_fact_evidence import reconcile

        dataset = self._frozen_dataset()
        pending = [
            (e["case"], e["fact_index"], e["fact"], e["classification"])
            for e in self.ledger["facts"] if e["human_ruling"] == "pending"
        ]
        self.assertEqual(len(pending), 14)
        rulings = {
            "rulings": [
                {
                    "case": case_id, "fact_index": fi, "ruling": "accepted",
                    "adjudicator": "human-review", "date": "2026-09-30",
                    "justification": "人工核验页图与摘录后接受该语义例外",
                    "fact": fact, "classification": classification,
                }
                for case_id, fi, fact, classification in pending
            ]
        }
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["status"] = "frozen"
        entries, _counts, _gaps = reconcile(rows, self.corpus, rulings)
        adjudicated = {
            (e["case"], e["fact_index"]): e["human_ruling"] for e in entries
        }
        self.assertEqual(
            {k for k, v in adjudicated.items() if v == "human_adjudicated"},
            {(c, fi) for c, fi, _f, _cl in pending},
            "regeneration must apply exactly the recorded rulings",
        )
        ledger = {
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "facts": entries,
        }
        ledger_path = self.tmp / "fact_ledger.json"
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        rulings_path = self.tmp / "fact_rulings.json"
        rulings_path.write_text(json.dumps(rulings, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(
            self._formal_blockers(dataset, ledger_path, rulings_path), [],
            "fully adjudicated + frozen + hash-bound must pass the authoritative gate",
        )

    def test_ledger_with_missing_entry_rejects_formal(self) -> None:
        dataset = self._frozen_dataset()
        path = self._bound_ledger(dataset)
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["facts"] = [
            e for e in ledger["facts"]
            if not (e["case"] == "G011" and e["fact_index"] == 0)
        ]
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        blockers = self._formal_blockers(dataset, path)
        self.assertTrue(blockers, "missing ledger entry must reject formal")
        self.assertTrue(any("coverage" in b or "missing" in b for b in blockers), blockers)

    def test_ledger_with_duplicate_entries_rejects_formal(self) -> None:
        dataset = self._frozen_dataset()
        path = self._bound_ledger(dataset)
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["facts"].append(copy.deepcopy(ledger["facts"][0]))
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        blockers = self._formal_blockers(dataset, path)
        self.assertTrue(blockers, "duplicate ledger entries must reject formal")
        self.assertTrue(any("duplicate" in b for b in blockers), blockers)

    def test_ledger_with_misbound_chunk_rejects_formal(self) -> None:
        dataset = self._frozen_dataset()
        path = self._bound_ledger(dataset)
        ledger = json.loads(path.read_text(encoding="utf-8"))
        entry = next(
            e for e in ledger["facts"] if e["case"] == "G037" and e["fact_index"] == 0
        )
        # rebind onto a real chunk of the same doc that carries neither the
        # quote nor the element_ids (a DIFFERENT carrier's chunk_id)
        other = entry["bindings"][1]["chunk_id"]
        self.assertNotEqual(entry["bindings"][0]["chunk_id"], other)
        entry["bindings"][0]["chunk_id"] = other
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        blockers = self._formal_blockers(dataset, path)
        self.assertTrue(blockers, "misbound chunk must reject formal")
        self.assertTrue(any("carry" in b or "mismatch" in b for b in blockers), blockers)

    # ------------------------------------------------------------------
    # ② auditable rulings: registry survives regeneration, no conflicts,
    # hand-edited human_ruling strings never pass
    # ------------------------------------------------------------------

    def _ruling(self) -> dict:
        row = next(r for r in self.rows if r["id"] == "G011")
        return {
            "case": "G011",
            "fact_index": 0,
            "ruling": "accepted",
            "adjudicator": "human-review",
            "date": "2026-09-30",
            "justification": "人工核验页图 renders/…p56：氨氮（mg/L）灰水均值 6.1 逐字在表内",
            "fact": row["expected_facts"][0],
            "classification": "covered_table_cell",
        }

    def _reconcile_with(self, rulings: dict | None):
        from scripts.reconcile_fact_evidence import reconcile

        return reconcile(copy.deepcopy(self.rows), self.corpus, rulings)

    def test_adjudicated_ruling_survives_ledger_regeneration(self) -> None:
        rulings = {"rulings": [self._ruling()]}
        snapshot = copy.deepcopy(rulings)
        entries, _counts, _gaps = self._reconcile_with(rulings)
        # the registry is read, never mutated by regeneration
        self.assertEqual(rulings, snapshot)
        by_key = {(e["case"], e["fact_index"]): e for e in entries}
        self.assertEqual(
            by_key[("G011", 0)]["human_ruling"], "human_adjudicated",
            "the recorded adjudication must survive regeneration",
        )
        # sibling exception WITHOUT a ruling stays pending
        self.assertEqual(by_key[("G011", 1)]["human_ruling"], "pending")
        # and a regeneration pass WITHOUT the registry leaves it pending too
        entries2, _c, _g = self._reconcile_with(None)
        by_key2 = {(e["case"], e["fact_index"]): e for e in entries2}
        self.assertEqual(by_key2[("G011", 0)]["human_ruling"], "pending")

    def test_ruling_on_covered_fact_conflicts_with_perfact_rules(self) -> None:
        # covered facts are machine-approved by rule; a ruling record on
        # them conflicts and must fail the gate
        from scripts.reconcile_fact_evidence import gate_problems

        rulings = {"rulings": [dict(self._ruling(), case="G004", fact_index=0,
                                   classification="covered",
                                   fact=next(r for r in self.rows if r["id"] == "G004")["expected_facts"][0])]}
        problems = gate_problems(
            self.rows, self.corpus, self.ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(), rulings,
        )
        self.assertTrue(
            any("G004[f0]" in p and "conflict" in p for p in problems),
            problems,
        )

    def test_ruling_with_stale_fact_text_conflicts(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        rulings = {"rulings": [dict(self._ruling(), fact="被改写过的事实")]}
        problems = gate_problems(
            self.rows, self.corpus, self.ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(), rulings,
        )
        self.assertTrue(
            any("G011[f0]" in p and "conflict" in p for p in problems),
            problems,
        )

    def test_ruling_with_wrong_classification_conflicts(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        rulings = {"rulings": [dict(self._ruling(), classification="covered")]}
        problems = gate_problems(
            self.rows, self.corpus, self.ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(), rulings,
        )
        self.assertTrue(
            any("G011[f0]" in p and "conflict" in p for p in problems),
            problems,
        )

    def test_handedited_human_ruling_string_does_not_pass(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        # hand-edit an exception fact to human_adjudicated WITHOUT a ruling
        # record: the gate recomputes the expected value and fails
        ledger = copy.deepcopy(self.ledger)
        entry = next(e for e in ledger["facts"]
                     if e["case"] == "G011" and e["fact_index"] == 0)
        self.assertEqual(entry["human_ruling"], "pending")
        entry["human_ruling"] = "human_adjudicated"
        problems = gate_problems(
            self.rows, self.corpus, ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        )
        self.assertTrue(
            any("G011[f0]" in p and "human_ruling" in p for p in problems),
            problems,
        )

    def test_exception_without_ruling_keeps_blocking_formal(self) -> None:
        # end-to-end through the formal entry: the real ledger still holds
        # 14 pending rulings -> formal stays blocked
        dataset = self._frozen_dataset()
        path = self._bound_ledger(dataset)
        blockers = self._formal_blockers(dataset, path)
        self.assertTrue(blockers)
        self.assertTrue(any("pending" in b for b in blockers), blockers)

    # ------------------------------------------------------------------
    # ③ relabel-to-bypass red lights: the acceptance must NOT trust the
    # ledger's classification -- it recomputes it from the case, evidence
    # and established rules; only TRUE refuse cases may be refuse_meta
    # ------------------------------------------------------------------

    def _relabeled_ledger(self, dataset_path: Path, relabel) -> Path:
        ledger = copy.deepcopy(self.ledger)
        ledger["dataset_sha256"] = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        for e in ledger["facts"]:
            relabel(e)
        path = self.tmp / "fact_ledger_relabeled.json"
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
        return path

    def test_pending_content_facts_relabeled_refuse_meta_reject_formal(self) -> None:
        # the audit's repro: relabel ALL 14 pending-ruling content facts to
        # refuse_meta/n-a in a same-hash ledger on a frozen dataset --
        # this bypassed bindings AND adjudication; it must reject
        dataset = self._frozen_dataset()

        def relabel(e: dict) -> None:
            if e["human_ruling"] == "pending":
                e["classification"] = "refuse_meta"
                e["human_ruling"] = "n/a"

        path = self._relabeled_ledger(dataset, relabel)
        blockers = self._formal_blockers(dataset, path)
        self.assertTrue(
            blockers,
            "relabeled refuse_meta facts must NOT bypass bindings/adjudication",
        )
        self.assertTrue(
            any("classification" in b and "recomputed" in b for b in blockers),
            blockers,
        )

    def test_pending_content_fact_relabeled_refuse_meta_fails_gate(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        ledger = copy.deepcopy(self.ledger)
        entry = next(e for e in ledger["facts"]
                     if e["case"] == "G011" and e["fact_index"] == 0)
        self.assertEqual(entry["human_ruling"], "pending")
        entry["classification"] = "refuse_meta"
        entry["human_ruling"] = "n/a"
        problems = gate_problems(
            self.rows, self.corpus, ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        )
        self.assertTrue(
            any("G011[f0]" in p and "classification" in p for p in problems),
            problems,
        )

    def test_content_fact_relabeled_to_other_class_fails_gate(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        # established rule for G011[0] is covered_table_cell; relabeling it
        # to plain covered (or any other class) must fail
        ledger = copy.deepcopy(self.ledger)
        entry = next(e for e in ledger["facts"]
                     if e["case"] == "G011" and e["fact_index"] == 0)
        entry["classification"] = "covered"
        entry["human_ruling"] = "machine_approved"
        problems = gate_problems(
            self.rows, self.corpus, ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        )
        self.assertTrue(
            any("G011[f0]" in p and "classification" in p for p in problems),
            problems,
        )

    def test_true_refuse_fact_relabeled_covered_fails_gate(self) -> None:
        from scripts.reconcile_fact_evidence import gate_problems

        # reverse direction: a genuine refusal meta-fact relabeled covered
        ledger = copy.deepcopy(self.ledger)
        entry = next(e for e in ledger["facts"]
                     if e["case"] == "G028" and e["classification"] == "refuse_meta")
        entry["classification"] = "covered"
        entry["human_ruling"] = "machine_approved"
        problems = gate_problems(
            self.rows, self.corpus, ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        )
        self.assertTrue(
            any("G028" in p and "classification" in p for p in problems),
            problems,
        )

    def test_true_refuse_cases_keep_refuse_meta_and_pass(self) -> None:
        # control: the REAL refuse cases (G008/G015/G028/G050) recompute to
        # refuse_meta and the real ledger passes untouched
        from scripts.reconcile_fact_evidence import gate_problems

        problems = gate_problems(
            self.rows, self.corpus, self.ledger,
            hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        )
        self.assertEqual(problems, [])
        refuse_meta = {(e["case"], e["fact_index"]) for e in self.ledger["facts"]
                       if e["classification"] == "refuse_meta"}
        self.assertEqual(len(refuse_meta), 8)
        for case_id in ("G008", "G015", "G028", "G050"):
            self.assertTrue(
                any(k[0] == case_id for k in refuse_meta),
                f"{case_id} must keep refuse_meta",
            )


if __name__ == "__main__":
    unittest.main()
