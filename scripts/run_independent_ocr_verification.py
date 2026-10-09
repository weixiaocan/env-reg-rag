"""Run a resumable, local PP-OCRv6 verification pass over OCR text pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from rapidocr import RapidOCR

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.independent_ocr_verifier import (  # noqa: E402
    mapped_bbox,
    sha256_file,
    verify_text_region,
)

MODEL_DIR = ROOT / "data/model_runtime/independent_ocr/rapidocr-3.9.2-ppocrv6"
MODEL_FILES = (
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "PP-OCRv6_det_small.onnx",
    "PP-OCRv6_rec_small.onnx",
)
DEFAULT_SCALE = 2.0
CONFIDENCE = 0.95


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"immutable evidence conflict: {path}")
        return
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--render-scale", type=float, default=DEFAULT_SCALE)
    args = parser.parse_args()
    if args.render_scale < 1.0 or args.render_scale > 4.0:
        raise SystemExit("--render-scale must be between 1.0 and 4.0")
    scale = float(args.render_scale)

    manifest_path = ROOT / "data/registry" / f"{args.corpus_version}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pi_ref = (manifest.get("page_intermediate_path") or
              manifest.get("canonical_documents") or
              (manifest.get("artifacts") or {}).get("page_intermediate_path"))
    if not pi_ref:
        raise KeyError("manifest does not identify the page-intermediate artifact")
    pi_path = ROOT / str(pi_ref)
    documents = [json.loads(line) for line in pi_path.read_text(encoding="utf-8").splitlines() if line]
    assets = {str(row["sha256"]): row for row in manifest["assets"]}
    model_hashes = {name: sha256_file(MODEL_DIR / name) for name in MODEL_FILES}
    structure_audit_path = (ROOT / str(manifest["structure_audit"]))
    structure_audit = json.loads(structure_audit_path.read_text(encoding="utf-8"))
    full_page_signal_keys = {
        (str(row.get("file_sha256") or ""), int(row.get("physical_page") or 0))
        for row in structure_audit.get("unresolved_items") or []
        if row.get("reason") == "original_full_page_image_layout_semantics_not_independently_verified"
    }
    config = {
        "engine": "RapidOCR", "package_version": "3.9.2",
        "detector": "PP-OCRv6_det_small", "recognizer": "PP-OCRv6_rec_small",
        "classifier": "PP-OCRv2_mobile", "render_scale": scale,
        "confidence_threshold": CONFIDENCE, "model_hashes": model_hashes,
        "region_alignment": "unique-contiguous-exact-subset-v2",
        "selection": (
            "page.extraction_route == full_ocr OR page has an unresolved "
            "original_full_page_image_layout_semantics_not_independently_verified signal"
        ),
        "structure_audit_sha256": sha256_file(structure_audit_path),
        "max_pages": args.max_pages,
    }
    run_hash = hashlib.sha256(json.dumps({"config": config, "page_intermediate_sha256": sha256_file(pi_path)},
                                        sort_keys=True).encode("utf-8")).hexdigest()
    run_dir = ROOT / "data/model_runtime/independent_ocr/runs" / f"ocrv6-{run_hash[:16]}"
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    engine = RapidOCR(params={"Global.model_root_dir": str(MODEL_DIR.resolve()),
                              "Global.log_level": "warning"})
    selected = [
        (doc, page)
        for doc in documents
        for page in doc.get("pages") or []
        if (page.get("extraction_route") == "full_ocr" or
            (str(doc["sha256"]), int(page["physical_page"])) in full_page_signal_keys)
    ]
    if args.max_pages is not None:
        selected = selected[:args.max_pages]
    verification_rows: list[dict] = []
    completed = 0
    for doc, page in selected:
        sha = str(doc["sha256"])
        number = int(page["physical_page"])
        output = pages_dir / sha / f"p{number:04d}.json"
        if output.is_file():
            payload = json.loads(output.read_text(encoding="utf-8"))
        else:
            reusable = None
            for prior in sorted((ROOT / "data/model_runtime/independent_ocr/runs").glob(
                    f"ocrv6-*/pages/{sha}/p{number:04d}.json")):
                try:
                    candidate = json.loads(prior.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeError):
                    continue
                if (candidate.get("file_sha256") == sha and
                        candidate.get("physical_page") == number and
                        candidate.get("model_hashes") == model_hashes and
                        (candidate.get("render") or {}).get("scale") == scale):
                    reusable = candidate
                    break
            if reusable is not None:
                payload = reusable
                _atomic_write(output, _json_bytes(payload))
            else:
                pdf_path = ROOT / str(assets[sha]["canonical_rel_path"])
                with pymupdf.open(pdf_path) as pdf:
                    pix = pdf[number - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                rotation_k = 3 if int(page.get("rotation") or 0) == 270 else 0
                ocr_image = np.rot90(image, rotation_k) if rotation_k else image
                result = engine(ocr_image)
                lines = []
                boxes = result.boxes if result.boxes is not None else ()
                texts = result.txts if result.txts is not None else ()
                scores = result.scores if result.scores is not None else ()
                for index, (box, text, score) in enumerate(zip(boxes, texts, scores), 1):
                    lines.append({
                        "line_id": f"l{index:04d}",
                        "bbox": mapped_bbox(box, rotation_k=rotation_k,
                                            original_width_px=pix.width,
                                            original_height_px=pix.height, scale=scale),
                        "text": str(text), "score": float(score),
                    })
                payload = {
                    "schema_version": "independent-ocr-page-v1",
                    "file_sha256": sha, "physical_page": number,
                    "page_rotation": int(page.get("rotation") or 0), "numpy_rotation_k": rotation_k,
                    "render": {"scale": scale, "width_px": pix.width, "height_px": pix.height,
                               "samples_sha256": hashlib.sha256(bytes(pix.samples)).hexdigest()},
                    "model_hashes": model_hashes, "lines": lines,
                }
                _atomic_write(output, _json_bytes(payload))
        for region in page.get("regions") or []:
            if region.get("kind") != "text" or region.get("execution_status") != "completed":
                continue
            row = verify_text_region(region, payload["lines"], confidence_threshold=CONFIDENCE)
            row.update({"file_sha256": sha, "physical_page": number,
                        "page_evidence_path": output.relative_to(ROOT).as_posix(),
                        "page_evidence_sha256": sha256_file(output), "model_hashes": model_hashes})
            verification_rows.append(row)
        completed += 1
        if completed % 25 == 0:
            print(f"completed {completed}/{len(selected)} pages", flush=True)

    verification_rows.sort(key=lambda row: str(row["region_id"]))
    verification_bytes = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                                    for row in verification_rows)
    _atomic_write(run_dir / "region_verifications.jsonl", verification_bytes)
    summary = {
        "schema_version": "independent-ocr-run-v1", "run_id": run_dir.name,
        "corpus_version": args.corpus_version, "config": config,
        "input_hashes": {"manifest": sha256_file(manifest_path), "page_intermediate": sha256_file(pi_path)},
        "selected_page_count": len(selected), "completed_page_count": completed,
        "region_verification_count": len(verification_rows),
        "verified_passed_count": sum(r["automatic_conclusion"] == "verified_passed" for r in verification_rows),
        "manual_review_required_count": sum(r["automatic_conclusion"] != "verified_passed" for r in verification_rows),
        "region_verifications_sha256": hashlib.sha256(verification_bytes).hexdigest(),
    }
    _atomic_write(run_dir / "run_manifest.json", _json_bytes(summary))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(run_dir.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
