"""E1 comparison vs the diagnostic baseline -- trace is the sole authority.

Reads the baseline run and an experiment run (control or rerank), computes:

* aggregate metrics from each run's ``summary.json`` (micro R@5/R@10 over
  the 77 must-hit evidence, citable / locator / document-level / macro /
  complete-case, strata macro);
* the fixed tracking set (baseline 14 missed evidence) transitions from the
  per-case traces: 救回 (None -> ≤10, sub-annotated @5/@10 with chunk and
  policy) / 不变 (still None);
* the full 77-must-hit guardrail transitions: 变差 (hit -> miss, ≤5 -> 6-10,
  rank rise ≥ 2), 改善 (6-10 -> ≤5, rank drop ≥ 2), 救回 (baseline miss ->
  hit), 不变 (everything else, including ±1 RRF tie jitter);
* the adoption gates from OPTIMIZATION_PLAN.md, each evaluated item by item.

For a control run (``rerank_enabled == false``) it additionally verifies
per-case final top-10 chunk-id equality against the baseline traces (the
rerank-off == baseline composition guarantee).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if p != str(ROOT)]
sys.path.insert(0, str(ROOT))

BASELINE_DIR = ROOT / "eval" / "runs" / "diagnostic-diag-baseline-20261001"
FIXED_TRACKING = {
    ("G001", 0), ("G004", 0), ("G018", 0), ("G018", 2), ("G019", 1),
    ("G020", 0), ("G023", 0), ("G023", 2), ("G023", 3), ("G026", 0),
    ("G027", 0), ("G030", 1), ("G040", 1), ("G047", 0),
}
WEAK_STRATA = ["challenge:同义表达", "modality:图片图注", "challenge:原文定位", "repr:定义"]
SIMILARITY_GUARDRAIL = "challenge:相似干扰"


def _net_effect_gate(worse: list[dict], saved: list[str], rescued: list[dict]) -> dict:
    """Compare regressions with unique miss->hit rescues.

    ``rescued`` is the fixed-tracking view of the same transitions already in
    the full-set ``saved`` list; adding both double-counts identical evidence.
    """
    saved_ids = set(saved)
    fixed_ids = {item["evidence"] for item in rescued}
    return {
        "worse": len(worse),
        "saved": len(saved_ids),
        "fixed_tracking_rescued": len(fixed_ids),
        "overlap": len(saved_ids & fixed_ids),
        "ok": len(worse) <= len(saved_ids),
    }


def _cases(run_dir: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((run_dir / "cases").glob("G*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        out[case["id"]] = case
    return out


def _ranks(case: dict) -> dict[int, int | None]:
    return {
        e["evidence_index"]: e["match_rank"]
        for e in case["evidence_results"]
    }


def _guardrail_transitions(base_cases: dict[str, dict], exp_cases: dict[str, dict]) -> dict:
    worse, better, saved, same = [], [], [], []
    for case_id in sorted(base_cases):
        base_ranks, exp_ranks = _ranks(base_cases[case_id]), _ranks(exp_cases[case_id])
        for index, base_rank in base_ranks.items():
            exp_rank = exp_ranks.get(index)
            key = f"{case_id}[{index}]"
            if base_rank is None:
                if exp_rank is not None:
                    saved.append(key)
                else:
                    same.append(key)
                continue
            if exp_rank is None:
                worse.append({"evidence": key, "from": base_rank, "to": None})
            elif base_rank <= 5 and exp_rank >= 6:
                worse.append({"evidence": key, "from": base_rank, "to": exp_rank})
            elif base_rank >= 6 and exp_rank <= 5:
                better.append({"evidence": key, "from": base_rank, "to": exp_rank})
            elif exp_rank - base_rank >= 2:
                worse.append({"evidence": key, "from": base_rank, "to": exp_rank})
            elif base_rank - exp_rank >= 2:
                better.append({"evidence": key, "from": base_rank, "to": exp_rank})
            else:
                same.append(key)
    return {"worse": worse, "better": better, "rescued": saved, "unchanged": same}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--baseline", type=Path, default=BASELINE_DIR)
    parser.add_argument("--write", action="store_true", help="write comparison_vs_baseline.json into the run dir")
    args = parser.parse_args()

    base_summary = json.loads((args.baseline / "summary.json").read_text(encoding="utf-8"))
    exp_summary = json.loads((args.run_dir / "summary.json").read_text(encoding="utf-8"))
    base_cases, exp_cases = _cases(args.baseline), _cases(args.run_dir)
    assert set(base_cases) == set(exp_cases), "case sets differ"

    comparison: dict = {
        "baseline_run_id": base_summary["run_id"],
        "experiment_run_id": exp_summary["run_id"],
        "classification": exp_summary.get("classification"),
        "rerank_enabled": exp_summary.get("rerank_enabled"),
        "metrics": {
            "baseline": {
                "micro_recall_at_5": base_summary["by_policy"]["_micro"]["recall_at_5"]
                if "_micro" in base_summary.get("by_policy", {})
                else base_summary["micro"]["recall_at_5"],
                "micro_recall_at_10": base_summary["micro"]["recall_at_10"],
                "citable": base_summary["by_policy"]["citable"],
                "locator_only": base_summary["by_policy"]["locator_only"],
                "macro_recall_at_5": base_summary["macro"]["recall_at_5"],
                "document_hit": {
                    "at_5": base_summary["macro"]["document_hit_at_5"],
                    "at_10": base_summary["macro"]["document_hit_at_10"],
                },
                "complete_case_rate": {
                    "at_5": base_summary["complete_case_rate_at_5"],
                    "at_10": base_summary["complete_case_rate_at_10"],
                },
                "citable_downgraded": {
                    "at_5": base_summary["by_policy"]["citable"]["downgraded_at_5"],
                    "at_10": base_summary["by_policy"]["citable"]["downgraded_at_10"],
                },
                "latency_ms": base_summary["latency_ms"],
            },
            "experiment": {
                "micro_recall_at_5": exp_summary["micro"]["recall_at_5"],
                "micro_recall_at_10": exp_summary["micro"]["recall_at_10"],
                "citable": exp_summary["by_policy"]["citable"],
                "locator_only": exp_summary["by_policy"]["locator_only"],
                "macro_recall_at_5": exp_summary["macro"]["recall_at_5"],
                "document_hit": {
                    "at_5": exp_summary["macro"]["document_hit_at_5"],
                    "at_10": exp_summary["macro"]["document_hit_at_10"],
                },
                "complete_case_rate": {
                    "at_5": exp_summary["complete_case_rate_at_5"],
                    "at_10": exp_summary["complete_case_rate_at_10"],
                },
                "citable_downgraded": {
                    "at_5": exp_summary["by_policy"]["citable"]["downgraded_at_5"],
                    "at_10": exp_summary["by_policy"]["citable"]["downgraded_at_10"],
                },
                "latency_ms": exp_summary["latency_ms"],
                "rerank_latency_ms": exp_summary.get("rerank_latency_ms"),
            },
        },
    }

    # -- fixed tracking set: 救回 / 不变
    rescued, unchanged = [], []
    for key in sorted(FIXED_TRACKING):
        case_id, index = key
        exp_rank = _ranks(exp_cases[case_id]).get(index)
        if exp_rank is None:
            unchanged.append(f"{case_id}[{index}]")
        else:
            entry = next(
                e for e in exp_cases[case_id]["evidence_results"]
                if e["evidence_index"] == index
            )
            rescued.append({
                "evidence": f"{case_id}[{index}]",
                "at": "5" if exp_rank <= 5 else "10",
                "rank": exp_rank,
                "chunk_id": entry.get("matched_chunk_id"),
                "policy": entry.get("hit_usage_policy"),
            })
    comparison["fixed_tracking_set"] = {
        "size": len(FIXED_TRACKING),
        "rescued": rescued,
        "unchanged": unchanged,
    }

    # -- full 77-must-hit guardrail transitions
    transitions = _guardrail_transitions(base_cases, exp_cases)
    worse = transitions["worse"]
    better = transitions["better"]
    saved = transitions["rescued"]
    same = transitions["unchanged"]
    comparison["guardrail_transitions_77"] = {
        "worse": worse,
        "better": better,
        "rescued": saved,
        "unchanged_count": len(same),
        "classified_count": len(worse) + len(better) + len(saved) + len(same),
    }

    # -- control run: final top-10 == baseline composition.  Cross-run the
    #    server-side RRF tie-break is only ±1 non-strictly stable (baseline
    #    report §3): order-identical / same-set ±1 reorder / rank-10 boundary
    #    flip are recorded separately -- metric-level equality is the gate.
    if exp_summary.get("rerank_enabled") is False:
        identical, reorder, boundary = [], [], []
        for case_id in sorted(base_cases):
            base_top = [h["chunk_id"] for h in base_cases[case_id]["hits"]]
            exp_top = [h["chunk_id"] for h in exp_cases[case_id]["hits"]]
            if base_top == exp_top:
                identical.append(case_id)
            elif set(base_top) == set(exp_top):
                reorder.append({
                    "case": case_id,
                    "max_positional_deviation": max(
                        abs(base_top.index(x) - exp_top.index(x)) for x in base_top
                    ),
                })
            else:
                boundary.append({
                    "case": case_id,
                    "baseline_only": sorted(set(base_top) - set(exp_top)),
                    "control_only": sorted(set(exp_top) - set(base_top)),
                })
        comparison["control_equivalence"] = {
            "micro_recall_identical": (
                comparison["metrics"]["baseline"]["micro_recall_at_5"]
                == comparison["metrics"]["experiment"]["micro_recall_at_5"]
                and comparison["metrics"]["baseline"]["micro_recall_at_10"]
                == comparison["metrics"]["experiment"]["micro_recall_at_10"]
            ),
            "order_identical_cases": identical,
            "same_set_reorder_cases": reorder,
            "boundary_flip_cases": boundary,
            "note": "服务端 RRF 并列分 tie-break 跨 run 非严格稳定（§3）：顺序级 47/50 同集合（24 题 ±1 换位）+ 3 题 top-10 边界翻转；指标级（@K 计数）必须一致，跨 run 对比只看 @K 计数",
        }

    # -- adoption gates (OPTIMIZATION_PLAN.md), item by item
    b, e = comparison["metrics"]["baseline"], comparison["metrics"]["experiment"]
    net_gain_5 = round(e["micro_recall_at_5"] * 77) - round(b["micro_recall_at_5"] * 77)
    net_gain_10 = round(e["micro_recall_at_10"] * 77) - round(b["micro_recall_at_10"] * 77)
    strata_base = base_summary.get("by_label") or {}
    strata_exp = exp_summary.get("by_label") or {}
    strata_check = {}
    for label in WEAK_STRATA + [SIMILARITY_GUARDRAIL]:
        b_val = (strata_base.get(label) or {}).get("macro", {}).get("recall_at_5")
        e_val = (strata_exp.get(label) or {}).get("macro", {}).get("recall_at_5")
        if b_val is None or e_val is None:
            strata_check[label] = {"baseline": b_val, "experiment": e_val, "drop": None, "ok": None}
            continue
        limit = 0.02 if label == SIMILARITY_GUARDRAIL else 0.05
        strata_check[label] = {
            "baseline": b_val, "experiment": e_val,
            "drop": round(b_val - e_val, 4), "limit": limit, "ok": (b_val - e_val) <= limit,
        }
    rerank_latency = e.get("rerank_latency_ms") or {}
    gates = {
        "recall_at_5_net_gain_ge_2": {"net_gain": net_gain_5, "ok": net_gain_5 >= 2},
        "recall_at_10_not_dropping": {"net_gain": net_gain_10, "ok": net_gain_10 >= 0},
        "citable_downgraded_not_new": {
            "at_5": {"baseline": b["citable_downgraded"]["at_5"], "experiment": e["citable_downgraded"]["at_5"],
                     "ok": e["citable_downgraded"]["at_5"] <= b["citable_downgraded"]["at_5"]},
            "at_10": {"baseline": b["citable_downgraded"]["at_10"], "experiment": e["citable_downgraded"]["at_10"],
                      "ok": e["citable_downgraded"]["at_10"] <= b["citable_downgraded"]["at_10"]},
        },
        "weak_strata": strata_check,
        "rerank_latency": (
            {"p50_ms": rerank_latency.get("p50"), "limit": 1500, "ok": (rerank_latency.get("p50") or 0) <= 1500,
             "p95_ms": rerank_latency.get("p95"), "p95_limit": 3000, "ok_p95": (rerank_latency.get("p95") or 0) <= 3000}
            if rerank_latency else None
        ),
        "document_hit_not_dropping": {
            "at_5": {"baseline": b["document_hit"]["at_5"], "experiment": e["document_hit"]["at_5"],
                     "ok": e["document_hit"]["at_5"] >= b["document_hit"]["at_5"]},
            "at_10": {"baseline": b["document_hit"]["at_10"], "experiment": e["document_hit"]["at_10"],
                      "ok": e["document_hit"]["at_10"] >= b["document_hit"]["at_10"]},
        },
        "net_effect_worse_le_saved": _net_effect_gate(worse, saved, rescued),
    }
    comparison["adoption_gates"] = gates

    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    if args.write:
        out = args.run_dir / "comparison_vs_baseline.json"
        out.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {out}")


if __name__ == "__main__":
    main()
