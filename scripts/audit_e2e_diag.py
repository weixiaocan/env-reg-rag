"""Audit a saved HTTP end-to-end diagnostic without making model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [path for path in sys.path if path != str(ROOT)]
sys.path.insert(0, str(ROOT))
DATASET = ROOT / "eval" / "golden_v2.jsonl"
POINTER = ROOT / "data" / "registry" / "corpus-current.json"
S2_CASES = ROOT / "eval" / "audits" / "s2_target_evidence" / "s2_per_case.jsonl"
DEFAULT_REVIEW = ROOT / "eval" / "audits" / "diagnostic_e2e_20261008_r1_agent_review.jsonl"

from scripts.audit_generation_diag import (  # noqa: E402
    _case_files_sha256,
    _required_citable_carriers,
    _rows,
)


def audit(run_dir: Path, review_path: Path) -> dict:
    golden_rows = _rows(DATASET)
    golden = {row["id"]: row for row in golden_rows}
    records = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "cases").glob("G*.json"))
    }
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    saved_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    traces = _rows(run_dir / "query-traces.jsonl")
    review_rows = _rows(review_path)
    review = {row["case"]: row for row in review_rows}
    review_meta_path = review_path.with_suffix(".meta.json")
    review_meta = json.loads(review_meta_path.read_text(encoding="utf-8"))
    s2 = {row["case"]: row for row in _rows(S2_CASES)}
    dataset_hash = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    s2_hash = hashlib.sha256(S2_CASES.read_bytes()).hexdigest()
    cases_hash = _case_files_sha256(run_dir / "cases")
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))

    problems: list[str] = []
    ids = set(golden)
    if set(records) != ids:
        problems.append("case file IDs do not equal golden IDs")
    if len(traces) != 50 or len({row.get("query_id") for row in traces}) != 50:
        problems.append("query trace count or identity is not 50 unique rows")
    if config.get("dataset_sha256") != dataset_hash:
        problems.append("config dataset hash differs from current golden_v2")
    if config.get("corpus_version") != pointer.get("corpus_version"):
        problems.append("config corpus differs from current pointer")
    if review_meta.get("source_case_files_sha256") != cases_hash:
        problems.append("semantic review is not bound to current case files")
    if any(record.get("trace_missing") for record in records.values()):
        problems.append("one or more case records have trace_missing=true")

    citation_problems: dict[str, list[str]] = {}
    for case_id, record in records.items():
        prompt = {
            item["evidence_id"]: item
            for item in ((record.get("prompt_input") or {}).get("prompt_evidence") or [])
        }
        row_problems: list[str] = []
        for claim in ((record.get("answer") or {}).get("claims") or []):
            cited = claim.get("evidence_ids") or []
            if not cited:
                row_problems.append(f"{claim.get('claim_id')}: no citation")
            for evidence_id in cited:
                item = prompt.get(evidence_id)
                if item is None:
                    row_problems.append(f"{claim.get('claim_id')}: citation outside prompt")
                elif item.get("usage_policy") != "answer_and_citation":
                    row_problems.append(f"{claim.get('claim_id')}: citation not citable")
        if row_problems:
            citation_problems[case_id] = row_problems

    content_ids = {row["id"] for row in golden_rows if row["expected_answerability"] != "refuse"}
    refusal_ids = ids - content_ids
    answered = {
        case_id for case_id in content_ids
        if records[case_id].get("status") in {"answered", "conflicting_sources"}
    }
    if set(review) != answered:
        problems.append("semantic review IDs do not equal answered content IDs")
    verdict_counts = Counter(row["verdict"] for row in review_rows)

    causal_rows: list[dict] = []
    for case_id in sorted(content_ids):
        requirements = _required_citable_carriers(s2[case_id])
        actual = set(records[case_id].get("generate_context_evidence_ids") or [])
        has_gold = bool(requirements) and all(requirements)
        all_in_context = has_gold and all(bool(group & actual) for group in requirements)
        verdict = review.get(case_id, {}).get("verdict")
        if not has_gold:
            category = "causal_unresolved_due_to_gold_preparation_gap"
        elif not all_in_context:
            category = "retrieval_or_admission_context_gap"
        elif case_id not in answered:
            category = "generation_over_refusal"
        elif verdict == "full":
            category = "success"
        else:
            category = "generation_quality_failure"
        causal_rows.append({"case": case_id, "category": category})

    correct_refusals = sorted(
        case_id for case_id in refusal_ids if records[case_id].get("status") == "refused"
    )
    return {
        "classification": "diagnostic saved HTTP end-to-end audit; NOT formal and NOT human gold",
        "source_run": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
        "dataset_sha256": dataset_hash,
        "s2_per_case_sha256": s2_hash,
        "corpus_version": config.get("corpus_version"),
        "identity": {
            "case_files": len(records), "trace_lines": len(traces),
            "source_case_files_sha256": cases_hash, "problems": problems,
        },
        "status_distribution": dict(Counter(row.get("status") for row in records.values())),
        "citation_binding": {"problem_case_count": len(citation_problems), "problems": citation_problems},
        "product_target_review": {
            "content_case_denominator": len(content_ids),
            "full_answers": verdict_counts["full"],
            "partial_answers": verdict_counts["partial"],
            "incorrect_answers": verdict_counts["incorrect"],
            "no_answer_content_cases": len(content_ids - answered),
            "refusal_case_denominator": len(refusal_ids),
            "correct_refusals": len(correct_refusals),
            "correct_refusal_case_ids": correct_refusals,
        },
        "bad_answer_examples": [row for row in review_rows if row["verdict"] != "full"],
        "observed_stage_outcomes": saved_summary.get("diagnostic_outcomes"),
        "causal_attribution": {
            "counts": dict(Counter(row["category"] for row in causal_rows)),
            "cases": causal_rows,
            "note": "stage outcome and causal attribution are separate; incomplete gold preparation remains unresolved",
        },
        "latency_ms": saved_summary.get("latency_ms"),
        "token_usage_totals": saved_summary.get("token_usage_totals"),
        "budget": saved_summary.get("budget"),
        "limitations": [
            "semantic verdicts are agent review against expected_facts, not independent human adjudication",
            "formal gate remains closed",
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default="eval/runs/diagnostic-e2e-20261008-r1")
    parser.add_argument("--review", default=str(DEFAULT_REVIEW.relative_to(ROOT)))
    parser.add_argument("--output", default="eval/audits/diagnostic_e2e_20261008_r1_audit.json")
    args = parser.parse_args()
    result = audit(ROOT / args.run_dir, ROOT / args.review)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (ROOT / args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
