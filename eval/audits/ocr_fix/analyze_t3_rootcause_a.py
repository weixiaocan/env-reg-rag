"""Build the read-only T3 primary-vs-independent OCR inventory."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
PROFILE = "1224404f0711"
RUN = "ocrv6-ef06411e1606ba35"
CASES = [
    ("c3c3df5ae9ca5bb77c34ee4506e194e4e588c8d5d0e14a3d43704ae710fdf9b1", 56),
    ("4336797dedd12e80c15ac93a2a6862a014fc0e06f493e3fcc89a6c9dbbccceed", 25),
]


def _norm(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value).lower()


def _intersection_ratio(box: list[float], region: list[float]) -> float:
    area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
    inter = max(0.0, min(box[2], region[2]) - max(box[0], region[0])) * max(
        0.0, min(box[3], region[3]) - max(box[1], region[1]))
    return inter / area if area else 0.0


def main() -> None:
    cases: list[dict[str, Any]] = []
    for sha, page in CASES:
        primary_path = ROOT / "data/canonical/pages" / sha / PROFILE / f"{page:04d}.json"
        independent_path = (ROOT / "data/model_runtime/independent_ocr/runs" / RUN /
                            "pages" / sha / f"p{page:04d}.json")
        primary = json.loads(primary_path.read_text(encoding="utf-8"))
        independent = json.loads(independent_path.read_text(encoding="utf-8"))
        elements = [element for element in primary.get("elements") or [] if _norm(
            str(element.get("text") or ""))]
        regions = [region for region in primary.get("regions") or [] if region.get("bbox")]
        comparisons = []
        for line in independent["lines"]:
            normalized = _norm(line["text"])
            candidates = [(difflib.SequenceMatcher(None, normalized, _norm(
                str(element.get("text") or ""))).ratio(), element) for element in elements]
            score, best = max(candidates, key=lambda pair: pair[0])
            overlaps = []
            for index, region in enumerate(regions):
                ratio = _intersection_ratio(line["bbox"], region["bbox"])
                if ratio > 0:
                    overlaps.append({"layout_index": index, "region_id": region["region_id"],
                                     "model_label": region.get("model_label"),
                                     "line_area_overlap_ratio": round(ratio, 6)})
            comparisons.append({
                **line, "best_primary_element_id": best.get("element_id"),
                "best_primary_text": best.get("text"), "similarity": round(score, 6),
                "primary_present_at_similarity_0_72": score >= 0.72,
                "overlapping_layout_regions": overlaps,
            })
        cases.append({
            "file_sha256": sha, "physical_page": page,
            "primary_path": primary_path.relative_to(ROOT).as_posix(),
            "independent_path": independent_path.relative_to(ROOT).as_posix(),
            "primary_profile": PROFILE,
            "layout_boxes": primary.get("layout_boxes") or [],
            "regions": [{key: region.get(key) for key in
                         ("region_id", "model_label", "bbox", "text")}
                        for region in primary.get("regions") or []],
            "elements": [{key: element.get(key) for key in
                          ("element_id", "type", "bbox", "text")}
                         for element in primary.get("elements") or []],
            "independent_lines": independent["lines"], "comparisons": comparisons,
        })
    output = ROOT / "eval/audits/ocr_fix/T3_rootcause_A_inventory.json"
    output.write_text(json.dumps({"schema_version": "t3-rootcause-a-v1", "cases": cases},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT), "cases=", len(cases))


if __name__ == "__main__":
    main()
