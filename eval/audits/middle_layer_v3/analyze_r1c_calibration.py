#!/usr/bin/env python
"""Compare fixed R1c calibration runs without invoking OCR or network access."""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
RUNS = ROOT / "data/model_runtime/independent_ocr/runs"
BASELINE = "ocrv6-ef06411e1606ba35"
SAMPLES = {
    "GB 55027-2022": {
        "sha256": "60bfe78380e9533e32ef659b054daf9fa7f072557824b7ae759b51371f1ca41f",
        "pages": [4, 8],
        "runs": {"150": "ocrv6-cabe9aea7d8c2c63", "120": "ocrv6-c45988ffcfe1e735",
                 "100": "ocrv6-33e3cd896a33e102"},
    },
    "GB/T 28592-2012": {
        "sha256": "f28bb600c89914f7611ed0b0818efdc485789c7b16463b770dcbd95cca4a8c81",
        "pages": [1, 4],
        "runs": {"150": "ocrv6-db7ce1146009ea49", "120": "ocrv6-4a3665c3f39800e8",
                 "100": "ocrv6-ac9624c9f0fef308"},
    },
    "GB 50268-2008": {
        "sha256": "fd10712cb0f7164f1d21a1607a3b0e4d036d015da0c9bca2c9f2771270b495ed",
        "pages": [34, 117],
        "runs": {"150": "ocrv6-771bea5332b26d58", "120": "ocrv6-2b462d7a5a0ba301",
                 "100": "ocrv6-ad663646b58ce0f8"},
    },
}
_PUNCT = re.compile(r"[\s,，。;；:：、()（）\[\]【】·•~～\-—_]+")


def main() -> int:
    rows = []
    for document, sample in SAMPLES.items():
        for page in sample["pages"]:
            baseline = _lines(BASELINE, sample["sha256"], page)
            rows.append({
                "document": document, "physical_page": page, "threshold": None,
                "baseline_run_id": BASELINE, "candidate_run_id": BASELINE,
                "baseline_line_count": len(baseline), "candidate_line_count": len(baseline),
                "potential_missing_count": 0, "potential_missing_lines": [],
                "changed_or_added_count": 0, "changed_or_added_lines": [],
            })
            for threshold, run_id in sample["runs"].items():
                candidate = _lines(run_id, sample["sha256"], page)
                missing = [line for line in baseline if not _matched(line, candidate)]
                added = [line for line in candidate if not _matched(line, baseline)]
                rows.append({
                    "document": document, "physical_page": page,
                    "threshold": None if threshold == "none" else int(threshold),
                    "baseline_run_id": BASELINE, "candidate_run_id": run_id,
                    "baseline_line_count": len(baseline), "candidate_line_count": len(candidate),
                    "potential_missing_count": len(missing),
                    "potential_missing_lines": missing,
                    "changed_or_added_count": len(added),
                    "changed_or_added_lines": added,
                })
    output = {
        "schema_version": "r1c-threshold-calibration/1",
        "method": ("Line-level normalized substring or SequenceMatcher >= 0.72 against the "
                   "same local-model no-preprocessing run; potential differences require original-page review."),
        "rows": rows,
    }
    target = ROOT / "eval/audits/middle_layer_v3/r1c_threshold_calibration.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(target), "row_count": len(rows)}, ensure_ascii=True))
    return 0


def _lines(run_id: str, sha256: str, page: int) -> list[str]:
    path = RUNS / run_id / "pages" / sha256 / f"p{page:04d}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [str(line.get("text") or "").strip() for line in payload.get("lines") or []
            if str(line.get("text") or "").strip()]


def _normalize(value: str) -> str:
    return _PUNCT.sub("", value).lower()


def _matched(value: str, candidates: list[str]) -> bool:
    source = _normalize(value)
    if not source:
        return True
    for candidate in candidates:
        target = _normalize(candidate)
        if source in target or target in source:
            return True
        if SequenceMatcher(None, source, target, autojunk=False).ratio() >= 0.72:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
