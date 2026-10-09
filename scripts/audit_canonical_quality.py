"""Create or check the immutable PDF -> Canonical source-object quality ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluation.canonical_quality_ledger import (  # noqa: E402
    build_quality_ledger,
    write_immutable_quality_report,
)
from src.evaluation.independent_ocr_verifier import (  # noqa: E402
    load_page_evidence,
    load_region_verifications,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--write", action="store_true",
                        help="write a content-addressed immutable report under data/quality")
    parser.add_argument("--require-ready-for-rechunk", action="store_true",
                        help="exit non-zero unless the quality gate is fully passed")
    parser.add_argument("--independent-ocr-run", type=Path,
                        help="completed local OCR run directory or region_verifications.jsonl")
    return parser.parse_args()


def main() -> int:
    args = _args()
    root = REPO_ROOT
    manifest_path = root / "data" / "registry" / f"{args.corpus_version}.json"
    if not manifest_path.is_file():
        raise SystemExit(f"manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("corpus_version") != args.corpus_version:
        raise SystemExit("manifest corpus_version mismatch")
    pi_path = root / str(manifest.get("canonical_documents") or "")
    if not pi_path.is_file():
        raise SystemExit(f"page-intermediate missing: {pi_path}")
    page_documents = [json.loads(line) for line in pi_path.open(encoding="utf-8") if line.strip()]
    canonical_dir = root / "data" / "canonical" / "v2" / args.corpus_version
    canonical_paths = sorted(canonical_dir.glob("*.canonical.json"))
    canonicals = [json.loads(path.read_text(encoding="utf-8")) for path in canonical_paths]
    def file_sha(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    independent = {}
    independent_pages = {}
    source_signals = []
    structure_audit_path = root / str(manifest.get("structure_audit") or "")
    if structure_audit_path.is_file():
        structure_audit = json.loads(structure_audit_path.read_text(encoding="utf-8"))
        source_signals = ((structure_audit.get("original_page_omission_check") or {})
                          .get("unassigned_source_signals") or [])
    if args.independent_ocr_run:
        evidence_path = args.independent_ocr_run
        if not evidence_path.is_absolute():
            evidence_path = root / evidence_path
        if evidence_path.is_dir():
            run_dir = evidence_path
            run_manifest = json.loads((evidence_path / "run_manifest.json").read_text(encoding="utf-8"))
            if run_manifest.get("corpus_version") != args.corpus_version:
                raise SystemExit("independent OCR run corpus_version mismatch")
            if run_manifest.get("completed_page_count") != run_manifest.get("selected_page_count"):
                raise SystemExit("independent OCR run is incomplete")
            independent_pages = load_page_evidence(run_dir)
            evidence_path = evidence_path / "region_verifications.jsonl"
        independent = load_region_verifications(evidence_path)
    report = build_quality_ledger(
        root, manifest, page_documents, canonicals,
        independent_text_verifications=independent,
        independent_page_evidence=independent_pages,
        source_signals=source_signals,
        artifact_input_hashes={
            "manifest_file": file_sha(manifest_path),
            "page_intermediate_file": file_sha(pi_path),
            **{f"canonical_file:{path.name}": file_sha(path) for path in canonical_paths},
            **({"structure_audit_file": file_sha(structure_audit_path)}
               if structure_audit_path.is_file() else {}),
        },
    )
    output = write_immutable_quality_report(root, report) if args.write else None
    print(json.dumps({
        "corpus_version": args.corpus_version,
        "input_manifest": str(manifest_path.relative_to(root)),
        "input_page_intermediate": str(pi_path.relative_to(root)),
        "canonical_document_count": len(canonicals),
        "independent_text_verification_count": len(independent),
        "independent_page_evidence_count": len(independent_pages),
        "source_signal_count": len(source_signals),
        "summary": report["summary"],
        "gate": report["gate"],
        "ledger_sha256": report["ledger_sha256"],
        "output": str(output.relative_to(root)) if output else None,
    }, ensure_ascii=False, indent=2))
    if args.require_ready_for_rechunk and report["gate"]["status"] != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
