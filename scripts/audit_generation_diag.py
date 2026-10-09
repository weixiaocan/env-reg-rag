"""Reproduce deterministic and agent semantic audit of a saved generation run."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "eval" / "golden_v2.jsonl"
POINTER = ROOT / "data" / "registry" / "corpus-current.json"
DEFAULT_REVIEW = ROOT / "eval" / "audits" / "diagnostic_gen_20261007_agent_review.jsonl"
DEFAULT_REVIEW_META = ROOT / "eval" / "audits" / "diagnostic_gen_20261007_agent_review.meta.json"
S2_PER_CASE = ROOT / "eval" / "audits" / "s2_target_evidence" / "s2_per_case.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _case_files_sha256(case_dir: Path) -> str:
    """Bind a manual semantic review to the exact saved case artifacts."""
    digest = hashlib.sha256()
    for path in sorted(case_dir.glob("G*.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _required_citable_carriers(s2_case: dict) -> list[set[str]]:
    required: list[set[str]] = []
    for fact in s2_case.get("facts", []):
        candidates = {
            carrier["chunk_id"]
            for carrier in fact.get("carriers", [])
            if carrier.get("supports_fact") is True
            and carrier.get("exists") is True
            and carrier.get("live_usage_policy") == "answer_and_citation"
        }
        required.append(candidates)
    return required


def audit(run_dir: Path, review_path: Path) -> dict:
    cases = _rows(DATASET)
    golden = {case["id"]: case for case in cases}
    review_rows = _rows(review_path)
    review = {row["case"]: row for row in review_rows}
    review_meta_path = review_path.with_suffix(".meta.json")
    review_meta = json.loads(review_meta_path.read_text(encoding="utf-8")) if review_meta_path.exists() else {}
    config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    records = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((run_dir / "cases").glob("G*.json"))
    }
    traces = _rows(run_dir / "query-traces.jsonl")
    judgments = _rows(run_dir / "judgments.jsonl")
    dataset_hash = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    case_files_sha256 = _case_files_sha256(run_dir / "cases")
    s2_sha256 = hashlib.sha256(S2_PER_CASE.read_bytes()).hexdigest()
    s2 = {row["case"]: row for row in _rows(S2_PER_CASE)}

    identity_problems: list[str] = []
    ids = set(golden)
    if set(records) != ids:
        identity_problems.append("case file IDs do not equal golden IDs")
    if len(traces) != len(cases) or len({t.get("query_id") for t in traces}) != len(cases):
        identity_problems.append("query traces are missing or duplicate")
    if len(judgments) != len(cases):
        identity_problems.append("judgment line count differs from golden cases")
    if config.get("dataset_sha256") != dataset_hash:
        identity_problems.append("config dataset hash differs from current golden_v2")
    if config.get("corpus_version") != pointer.get("corpus_version"):
        identity_problems.append("config corpus differs from current published pointer")
    if review_meta.get("source_case_files_sha256") != case_files_sha256:
        identity_problems.append("agent semantic review is not bound to the current saved case files")

    citation_problem_cases: dict[str, list[str]] = {}
    for case_id, record in records.items():
        prompt = {
            item["evidence_id"]: item
            for item in ((record.get("prompt_input") or {}).get("prompt_evidence") or [])
        }
        problems: list[str] = []
        for claim in ((record.get("answer") or {}).get("claims") or []):
            cited = claim.get("evidence_ids") or []
            if not cited:
                problems.append(f"{claim.get('claim_id')}: no citation")
            for evidence_id in cited:
                item = prompt.get(evidence_id)
                if item is None:
                    problems.append(f"{claim.get('claim_id')}: {evidence_id} not in prompt")
                elif item.get("usage_policy") != "answer_and_citation":
                    problems.append(f"{claim.get('claim_id')}: {evidence_id} is not citable")
        if problems:
            citation_problem_cases[case_id] = problems

    content_ids = {c["id"] for c in cases if c["expected_answerability"] != "refuse"}
    refusal_ids = ids - content_ids
    answered_content = {
        case_id for case_id in content_ids
        if records[case_id].get("status") in {"answered", "conflicting_sources"}
    }
    expected_review_ids = answered_content
    if set(review) != expected_review_ids:
        identity_problems.append("agent semantic review IDs do not equal answered content case IDs")

    verdict_counts = Counter(row["verdict"] for row in review_rows)
    no_answer_content = sorted(content_ids - answered_content)
    refusal_correct = sorted(case_id for case_id in refusal_ids if records[case_id].get("status") == "refused")
    failure_rows: list[dict] = []
    review_verdict = {row["case"]: row["verdict"] for row in review_rows}
    for case_id in sorted(content_ids):
        record = records[case_id]
        s2_case = s2[case_id]
        requirements = _required_citable_carriers(s2_case)
        actual_context = set(record.get("generate_context_evidence_ids") or [])
        has_gold_evidence = bool(requirements) and all(requirement for requirement in requirements)
        all_gold_in_context = has_gold_evidence and all(
            bool(requirement & actual_context) for requirement in requirements
        )
        verdict = review_verdict.get(case_id)
        if not has_gold_evidence:
            category = "causal_unresolved_due_to_gold_preparation_gap"
        elif not all_gold_in_context:
            category = "retrieval_or_admission_context_gap"
        elif case_id not in answered_content:
            category = "generation_over_refusal"
        elif verdict == "full":
            category = "success"
        else:
            category = "generation_quality_failure"
        failure_rows.append({
            "case": case_id,
            "category": category,
            "saved_status": record.get("status"),
            "refusal_stage": record.get("refusal_stage"),
            "s2_completeness": s2[case_id].get("completeness"),
        })
    failure_counts = Counter(row["category"] for row in failure_rows)
    return {
        "classification": "diagnostic saved-run audit; deterministic checks plus agent semantic review; NOT formal and NOT human gold",
        "source_run": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
        "dataset_sha256": dataset_hash,
        "s2_per_case_sha256": s2_sha256,
        "corpus_version": config.get("corpus_version"),
        "identity": {
            "golden_cases": len(cases),
            "case_files": len(records),
            "trace_lines": len(traces),
            "judgment_lines": len(judgments),
            "problems": identity_problems,
            "source_case_files_sha256": case_files_sha256,
            "semantic_review_meta": str(review_meta_path.relative_to(ROOT)).replace("\\", "/"),
        },
        "run_layer": "production retrieval -> generation application service (not retrieval-bypassed generation; not HTTP)",
        "status_distribution": dict(Counter(record.get("status") for record in records.values())),
        "citation_binding": {
            "answered_cases": sum(1 for record in records.values() if record.get("answer")),
            "problem_case_count": len(citation_problem_cases),
            "problems": citation_problem_cases,
        },
        "product_target_review": {
            "content_case_denominator": len(content_ids),
            "full_answers": verdict_counts["full"],
            "partial_answers": verdict_counts["partial"],
            "incorrect_answers": verdict_counts["incorrect"],
            "no_answer_content_cases": len(no_answer_content),
            "no_answer_case_ids": no_answer_content,
            "refusal_case_denominator": len(refusal_ids),
            "correct_refusals": len(refusal_correct),
            "correct_refusal_case_ids": refusal_correct,
            "semantic_review_artifact": str(review_path.relative_to(ROOT)).replace("\\", "/"),
        },
        "bad_answer_examples": [row for row in review_rows if row["verdict"] != "full"],
        "failure_attribution": {
            "content_case_denominator": len(content_ids),
            "counts": dict(failure_counts),
            "cases": failure_rows,
            "method": "output quality remains a separate semantic verdict; causal attribution first requires a complete corrected-S2 supported+citable carrier set, then compares each fact carrier with the saved generation context",
        },
        "limitations": [
            "semantic verdicts are an agent review against current expected_facts, not independent human adjudication",
            "the original numeric-only judgment did not evaluate non-numeric facts or locate-labeled content cases",
            "provider pricing was not configured, so saved token usage cannot be converted to a verified cost",
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default="eval/runs/diagnostic-gen-20261007")
    parser.add_argument("--review", default=str(DEFAULT_REVIEW.relative_to(ROOT)))
    parser.add_argument("--output")
    args = parser.parse_args()
    result = audit(ROOT / args.run_dir, ROOT / args.review)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        (ROOT / args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
