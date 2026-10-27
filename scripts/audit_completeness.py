"""Completeness audit: is everything printed on the pages in the corpus?

Two independent checks over one corpus version:

1. Page coverage -- each line of an independent reading of the page
   (independent OCR run for OCR pages, PDF text layer otherwise) must be found
   in the canonical document (``src/application/page_coverage.py``).
2. Structure cross-checks -- formula lead-ins, "式中", printed equation numbers
   and same-document figure/table references must resolve
   (``src/application/structure_checks.py``).

Findings that are legitimately not corpus content (an external reference, an
OCR misread of a line the corpus holds) are listed with a reason in
``data/registry/completeness-exceptions.json``. The publish gate requires zero
open findings.

    python scripts/audit_completeness.py --corpus-version corpus-xxxx \
        --independent-ocr-run-id ocrv6-c454d03f07c7e5c4 [--fail-on-open]

Writes ``data/canonical/v2/<version>/completeness.json``, bound to the
canonical files and the exceptions registry it was computed against; the
publish gate refuses a missing, stale or non-zero report.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.page_coverage import (  # noqa: E402
    audit_page, page_references, recurring_lines, summarize, toc_pages,
)
from src.application.publish_state import (  # noqa: E402
    COMPLETENESS_EXCEPTIONS, write_completeness_report,
)
from src.application.structure_checks import (  # noqa: E402
    apply_exceptions, check_equation_numbers, check_lead_ins, check_references,
)

EXCEPTIONS = ROOT / COMPLETENESS_EXCEPTIONS
OCR_RUNS = ROOT / "data" / "model_runtime" / "independent_ocr" / "runs"
WATERMARK_CONFIG = ROOT / "config" / "middle_layer_v3.json"


def _raw_pdfs() -> dict[str, Path]:
    return {p.name: p for p in (ROOT / "data" / "raw").rglob("*.pdf")}


def _native_lines(pdf_path: Path) -> dict[int, list[dict]]:
    import pymupdf
    pages: dict[int, list[dict]] = {}
    with pymupdf.open(pdf_path) as doc:
        for index, page in enumerate(doc, 1):
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if text:
                        lines.append({"text": text, "bbox": list(line["bbox"])})
            pages[index] = lines
    return pages


def _ocr_lines(run_id: str, sha: str) -> dict[int, list[dict]]:
    folder = OCR_RUNS / run_id / "pages" / sha
    pages: dict[int, list[dict]] = {}
    if not folder.is_dir():
        return pages
    for path in sorted(folder.glob("p*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        pages[int(path.stem[1:])] = [
            {"text": line["text"], "bbox": line["bbox"]} for line in data.get("lines") or []]
    return pages


def load_exceptions(path: Path = EXCEPTIONS) -> tuple[dict[str, str], dict[str, str]]:
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = {row["line_key"]: row["reason"] for row in data.get("lines") or []}
    findings = {row["key"]: row["reason"] for row in data.get("findings") or []}
    return lines, findings


def require_complete_canonical_set(root: Path, corpus_version: str) -> list[Path]:
    canon_dir = root / "data" / "canonical" / "v2" / corpus_version
    docs = sorted(canon_dir.glob("*.canonical.json"))
    if not docs:
        raise FileNotFoundError(f"no canonical documents under {canon_dir}")
    manifest = json.loads((root / "data" / "registry" / f"{corpus_version}.json").read_text(encoding="utf-8"))
    expected = {f"{asset['sha256']}.canonical.json" for asset in manifest["assets"]}
    actual = {path.name for path in docs}
    if actual != expected:
        raise ValueError(f"incomplete canonical set: {len(actual)} of {len(expected)} documents; "
                         f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}")
    return docs


def audit(corpus_version: str, ocr_run_id: str, *, second_ocr_run_id: str | None = None,
          verbose: bool = True) -> dict:
    docs = require_complete_canonical_set(ROOT, corpus_version)
    watermarks = json.loads(WATERMARK_CONFIG.read_text(encoding="utf-8")).get("watermark_exact_lines") or []
    line_exceptions, finding_exceptions = load_exceptions()
    pdfs = _raw_pdfs()
    report_docs = []
    totals: dict[str, int] = {}
    open_findings = 0
    for path in docs:
        canonical = json.loads(path.read_text(encoding="utf-8"))
        sha = canonical["source"]["sha256"]
        name = canonical["source"]["file_name"]
        elements = canonical.get("elements") or []
        refs = page_references(canonical)
        tocs = toc_pages(canonical)
        ocr = _ocr_lines(ocr_run_id, sha)
        native = _native_lines(pdfs[name]) if name in pdfs else {}
        second = _ocr_lines(second_ocr_run_id, sha) if second_ocr_run_id else {}
        page_meta = {int(p["physical_page"]): p for p in canonical.get("pages") or []}
        chosen: dict[int, tuple[str, list[dict]]] = {}
        for page, meta in page_meta.items():
            if page in ocr and meta.get("extraction_route") != "native":
                chosen[page] = ("independent_ocr", ocr[page])
            elif native.get(page):
                chosen[page] = ("pdf_text_layer", native[page])
            elif page in ocr:
                chosen[page] = ("independent_ocr", ocr[page])
        recurring = recurring_lines(
            {p: (lines, float(page_meta[p].get("height") or 0)) for p, (_, lines) in chosen.items()})
        doc_pages = []
        doc_counts: dict[str, int] = {}
        unread = [p for p in page_meta if p not in chosen]
        for page in sorted(chosen):
            source, lines = chosen[page]
            meta = page_meta[page]
            verdicts = audit_page(
                sha=sha, page=page, lines=lines, reference=refs.get(page),
                page_width=float(meta.get("width") or 0), page_height=float(meta.get("height") or 0),
                watermarks=watermarks, recurring=recurring, exceptions=line_exceptions,
                toc_page=page in tocs,
                alternates=[r for r in (second.get(page), native.get(page) if source != "pdf_text_layer" else None,
                                        ocr.get(page) if source != "independent_ocr" else None) if r])
            for k, v in summarize(verdicts).items():
                doc_counts[k] = doc_counts.get(k, 0) + v
            missing = [v for v in verdicts if v.status == "missing"]
            if missing:
                doc_pages.append({
                    "physical_page": page, "reading_source": source,
                    "missing": [{"text": v.text, "bbox": [round(x, 1) for x in v.bbox],
                                 "coverage": v.coverage, "line_key": v.key} for v in missing]})
        findings = (check_lead_ins(sha, elements)
                    + check_equation_numbers(
                        sha, elements, {p: lines for p, (_, lines) in chosen.items()},
                        {p: float(m.get("width") or 0) for p, m in page_meta.items()}, tocs)
                    + check_references(sha, elements))
        open_, excepted = apply_exceptions(findings, finding_exceptions)
        open_findings += len(open_)
        for k, v in doc_counts.items():
            totals[k] = totals.get(k, 0) + v
        report_docs.append({
            "sha256": sha, "file_name": name, "line_counts": doc_counts,
            "pages_without_independent_reading": unread, "missing_lines": doc_pages,
            "open_findings": [asdict(f) for f in open_],
            "excepted_findings": [dict(asdict(f), reason=r) for f, r in excepted]})
        if verbose:
            print(f"{name[:44]:46s} missing lines {doc_counts.get('missing', 0):4d}  "
                  f"open findings {len(open_):3d}  unread pages {len(unread)}")
    return {"corpus_version": corpus_version, "independent_ocr_run_id": ocr_run_id,
            "second_ocr_run_id": second_ocr_run_id,
            "line_totals": totals, "missing_lines": totals.get("missing", 0),
            "open_findings": open_findings, "documents": report_docs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--independent-ocr-run-id", required=True)
    parser.add_argument("--second-ocr-run-id",
                        help="another OCR pass used only to confirm a line is a misread")
    parser.add_argument("--out")
    parser.add_argument("--fail-on-open", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit(args.corpus_version, args.independent_ocr_run_id,
                       second_ocr_run_id=args.second_ocr_run_id)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        out = write_completeness_report(ROOT, args.corpus_version, report)
    print("line totals", report["line_totals"])
    print("missing lines", report["missing_lines"], "open findings", report["open_findings"])
    print("report", out)
    if args.fail_on_open and (report["missing_lines"] or report["open_findings"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
