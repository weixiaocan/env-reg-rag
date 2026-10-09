"""Run a retrieval-bypassed generation diagnostic on the same 50 cases.

Only cases whose every expected fact has a saved ``supported=true`` citable
binding carrier receive evidence; retrieval rank is intentionally ignored.
Cases without fully prepared answer evidence are retained in the denominator
as ``evidence_preparation_insufficient``; locator-only evidence is never
silently elevated.  Refusal cases receive an empty evidence pack.  This is a
diagnostic, not a formal golden-set score and not an HTTP end-to-end run.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

from scripts.run_generation_diag import (  # noqa: E402
    DATASET,
    POINTER,
    RecordingAnswerGenerator,
    _binding_problems,
    _claim_support_problems,
    _dataset_sha256,
    _expected_facts_coverage,
)
from src.adapters.llm_provider import (  # noqa: E402
    LlmProviderSettings,
    build_answer_generator,
)
from src.domain.query import EvidenceItem, EvidencePack, QueryRequest  # noqa: E402
from scripts.external_run_guard import (  # noqa: E402
    add_verified_cost,
    can_start_call,
    validate_external_run,
)

S2_CASES = ROOT / "eval" / "audits" / "s2_target_evidence" / "s2_per_case.jsonl"
SEND_SCOPE = "gold-evidence:8-cases:questions-and-exact-saved-citable-chunks"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_gold_pack(
    case: dict,
    s2_case: dict,
    corpus_version: str,
) -> tuple[EvidencePack | None, str | None]:
    if case["expected_answerability"] == "refuse":
        return EvidencePack(corpus_version=corpus_version, evidence=[]), None
    selected: dict[str, dict] = {}
    for fact in s2_case.get("facts") or []:
        supported_ids = {
            binding["chunk_id"]
            for binding in (fact.get("bindings") or [])
            if binding.get("supported") is True
        }
        candidates = [carrier for carrier in (fact.get("carriers") or [])
                      if carrier.get("chunk_id") in supported_ids
                      and carrier.get("supports_fact") is True
                      and carrier.get("live_usage_policy") == "answer_and_citation"
                      and carrier.get("exists") is True
                      and carrier.get("text")]
        if not candidates:
            return None, "evidence_preparation_insufficient"
        candidates.sort(key=lambda item: item["chunk_id"])
        selected[candidates[0]["chunk_id"]] = candidates[0]

    evidence: list[EvidenceItem] = []
    for chunk_id, carrier in selected.items():
        pages = list(carrier.get("physical_pages") or [])
        file_name = carrier["file_name"]
        evidence.append(EvidenceItem(
            evidence_id=chunk_id,
            text=carrier["text"],
            document_version_id=f"{case.get('anchor_corpus', corpus_version)}:{file_name}",
            file_name=file_name,
            physical_pages=pages,
            heading_path=list(carrier.get("heading_path") or []),
            usage_policy="answer_and_citation",
            source_uri=(f"local://{file_name}#p{pages[0]}" if pages else f"local://{file_name}"),
        ))
    if not evidence:
        return None, "evidence_preparation_insufficient"
    return EvidencePack(corpus_version=corpus_version, evidence=evidence), None


def _citation_problems(answer, pack: EvidencePack) -> list[str]:
    citable = {item.evidence_id for item in pack.evidence if item.usage_policy == "answer_and_citation"}
    problems: list[str] = []
    claimed: set[str] = set()
    for claim in answer.claims:
        ids = set(claim.evidence_ids)
        claimed.update(ids)
        if not ids or not ids.issubset(citable):
            problems.append(f"{claim.claim_id}: citation ids are missing or outside provided evidence")
    by_id = {item.evidence_id: item for item in pack.evidence}
    for conflict in answer.conflicts:
        ids = set(conflict.evidence_ids)
        versions = {by_id[eid].document_version_id for eid in ids if eid in by_id}
        if len(ids) < 2 or not ids.issubset(citable) or len(versions) < 2 or not ids.issubset(claimed):
            problems.append(f"{conflict.conflict_id}: invalid conflict evidence binding")
    return problems


async def run_case(generator, case: dict, pack: EvidencePack | None, run_id: str, unavailable_reason: str | None) -> dict:
    record = {
        "case": case["id"],
        "question": case["question"],
        "expected_answerability": case["expected_answerability"],
        "expected_facts": list(case.get("expected_facts") or []),
        "status": unavailable_reason,
        "model_called": False,
        "provided_evidence": [],
        "answer": None,
        "citation_problems": [],
        "claim_support_problems": [],
        "token_usage": {},
        "generation_ms": None,
        "failure_class": None,
    }
    if pack is None:
        return record
    record["provided_evidence"] = [
        {
            "evidence_id": item.evidence_id,
            "text": item.text,
            "file_name": item.file_name,
            "physical_pages": item.physical_pages,
            "usage_policy": item.usage_policy,
        }
        for item in pack.evidence
    ]
    request = QueryRequest(
        request_id=f"{run_id}-{case['id']}",
        question=case["question"],
        conversation_context={},
        corpus_version=pack.corpus_version,
        caller_type="evaluation",
    )
    started = time.perf_counter()
    record["model_called"] = True
    try:
        result = await generator.generate(request=request, evidence_pack=pack)
        record["generation_ms"] = round((time.perf_counter() - started) * 1000, 3)
        answer = result.answer
        record["token_usage"] = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
            "estimated_cost": result.usage.estimated_cost,
            "currency": result.usage.currency,
            "pricing_status": result.usage.pricing_status,
        }
        if not answer.claims and not answer.conflicts:
            record["status"] = "refused"
            return record
        record["answer"] = {
            "claims": [{"claim_id": c.claim_id, "text": c.text, "evidence_ids": list(c.evidence_ids)} for c in answer.claims],
            "conflicts": [{"conflict_id": c.conflict_id, "description": c.description, "evidence_ids": list(c.evidence_ids)} for c in answer.conflicts],
        }
        record["status"] = "conflicting_sources" if answer.conflicts else "answered"
        record["citation_problems"] = _citation_problems(answer, pack)
        record["claim_support_problems"] = _claim_support_problems({
            "answer": record["answer"],
            "prompt_input": {"prompt_evidence": record["provided_evidence"]},
        })
        record["expected_facts_coverage"] = _expected_facts_coverage(case, record)
    except Exception as exc:  # noqa: BLE001 -- every case must leave a trace
        record["generation_ms"] = round((time.perf_counter() - started) * 1000, 3)
        record["status"] = "error"
        record["failure_class"] = "generation_error"
        record["error_type"] = type(exc).__name__
        record["error_message"] = str(exc)[:500]
    return record


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 2)


def build_summary(records: list[dict]) -> dict:
    called = [r for r in records if r["model_called"]]
    latencies = [r["generation_ms"] for r in called if r.get("generation_ms") is not None]
    statuses = {status: sum(1 for r in records if r["status"] == status) for status in sorted({r["status"] for r in records})}
    return {
        "classification": "diagnostic (given gold evidence; retrieval bypassed; NOT HTTP; NOT formal)",
        "denominators": {
            "all_cases": len(records),
            "content_target_cases": sum(1 for r in records if r["expected_answerability"] != "refuse"),
            "refusal_cases": sum(1 for r in records if r["expected_answerability"] == "refuse"),
            "model_called": len(called),
            "evidence_preparation_insufficient": sum(1 for r in records if r["status"] == "evidence_preparation_insufficient"),
        },
        "status_distribution": statuses,
        "citation_problem_cases": [r["case"] for r in records if r.get("citation_problems")],
        "claim_support_problem_cases": [r["case"] for r in records if r.get("claim_support_problems")],
        "latency_ms": {"generation": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95), "mean": round(sum(latencies) / len(latencies), 2) if latencies else 0.0, "n": len(latencies)}},
        "token_usage_totals": {
            "input_tokens": sum((r.get("token_usage") or {}).get("input_tokens") or 0 for r in records),
            "output_tokens": sum((r.get("token_usage") or {}).get("output_tokens") or 0 for r in records),
            "total_tokens": sum((r.get("token_usage") or {}).get("total_tokens") or 0 for r in records),
            "estimated_cost": round(sum(
                (r.get("token_usage") or {}).get("estimated_cost") or 0 for r in records
            ), 8),
            "currencies": sorted({
                (r.get("token_usage") or {}).get("currency") for r in records
                if (r.get("token_usage") or {}).get("currency")
            }),
            "pricing_statuses": sorted({
                (r.get("token_usage") or {}).get("pricing_status") for r in records
                if (r.get("token_usage") or {}).get("pricing_status")
            }),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authorization-note")
    parser.add_argument("--budget-cny", type=float)
    parser.add_argument("--per-call-reserve-cny", type=float)
    parser.add_argument("--authorized-provider")
    parser.add_argument("--authorized-model")
    parser.add_argument("--authorized-base-url")
    parser.add_argument("--authorized-send-scope")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate bindings and evidence readiness without loading provider settings or calling a model",
    )
    args = parser.parse_args()

    cases = _rows(DATASET)
    s2_by_case = {row["case"]: row for row in _rows(S2_CASES)}
    pointer = json.loads(POINTER.read_text(encoding="utf-8"))
    problems = _binding_problems(cases, pointer)
    if problems:
        raise SystemExit(json.dumps({"binding_problems": problems}, ensure_ascii=False))

    prepared: dict[str, tuple[EvidencePack | None, str | None]] = {}
    for case in cases:
        prepared[case["id"]] = build_gold_pack(
            case, s2_by_case.get(case["id"], {}), pointer["corpus_version"]
        )
    s2_sha256 = hashlib.sha256(S2_CASES.read_bytes()).hexdigest()
    if args.preflight_only:
        ready = [case["id"] for case in cases if prepared[case["id"]][0] is not None]
        insufficient = [case["id"] for case in cases if prepared[case["id"]][1] is not None]
        preflight = {
            "classification": "offline preflight; no provider loaded; no model call",
            "dataset_sha256": _dataset_sha256(),
            "s2_per_case_sha256": s2_sha256,
            "corpus_version": pointer["corpus_version"],
            "all_cases": len(cases),
            "model_call_ready": len(ready),
            "ready_cases": ready,
            "evidence_preparation_insufficient": len(insufficient),
            "insufficient_cases": insufficient,
        }
        preflight_path = ROOT / "eval" / "audits" / f"{args.run_id}-preflight.json"
        preflight_path.write_text(
            json.dumps(preflight, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        preflight["artifact"] = str(preflight_path.relative_to(ROOT)).replace("\\", "/")
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    settings = LlmProviderSettings.from_env()
    try:
        authorization = validate_external_run(
            settings,
            expected_scope=SEND_SCOPE,
            provider=args.authorized_provider,
            model=args.authorized_model,
            base_url=args.authorized_base_url,
            send_scope=args.authorized_send_scope,
            note=args.authorization_note,
            budget_cny=args.budget_cny,
            per_call_reserve_cny=args.per_call_reserve_cny,
        )
    except ValueError as exc:
        raise SystemExit(f"external run blocked: {exc}") from exc
    recording = RecordingAnswerGenerator(build_answer_generator(settings))
    run_dir = ROOT / "eval" / "runs" / args.run_id
    if run_dir.exists():
        raise SystemExit(f"run dir already exists: {run_dir}")
    (run_dir / "cases").mkdir(parents=True)

    config = {
        "classification": "diagnostic",
        "layer": "given_gold_evidence_generation",
        "retrieval": "bypassed",
        "run_id": args.run_id,
        "dataset_sha256": _dataset_sha256(),
        "s2_per_case_sha256": s2_sha256,
        "dataset_cases": len(cases),
        "corpus_version": pointer["corpus_version"],
        "evidence_source": "S2 supported=true citable binding carriers with exact saved chunk payloads; retrieval rank ignored",
        "provider": settings.provider,
        "model": settings.model,
        "temperature": settings.temperature,
        "pricing": {
            "input_cost_per_million": settings.input_cost_per_million,
            "output_cost_per_million": settings.output_cost_per_million,
            "currency": settings.cost_currency,
            "status": "configured and required by external-run guard",
        },
        "external_data_authorized": True,
        "authorization": authorization.to_record(),
        "budget_guard_limit": "per-call reserve is checked before dispatch; actual calculated cost is checked after response, so this is not a provider-side hard spending cap",
    }
    (run_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    records: list[dict] = []
    spent_cny = 0.0
    for case in cases:
        pack, reason = prepared[case["id"]]
        if reason is None and not can_start_call(spent_cny, authorization):
            reason = "budget_not_run"
            pack = None
        record = asyncio.run(run_case(recording, case, pack, args.run_id, reason))
        if record["model_called"]:
            try:
                spent_cny = add_verified_cost(
                    spent_cny, record.get("token_usage") or {}, authorization
                )
            except ValueError as exc:
                record["budget_guard_error"] = str(exc)
                record["diagnostic_outcome"] = "budget_guard_failure"
        record["cumulative_estimated_cost_cny"] = round(spent_cny, 8)
        records.append(record)
        (run_dir / "cases" / f"{record['case']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        with (run_dir / "query-traces.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if record.get("budget_guard_error"):
            break
    summary = build_summary(records)
    summary["budget"] = {
        "authorized_cny": authorization.budget_cny,
        "per_call_reserve_cny": authorization.per_call_reserve_cny,
        "estimated_spent_cny": round(spent_cny, 8),
        "completed_case_records": len(records),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
