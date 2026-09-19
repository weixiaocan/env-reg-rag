"""Recognize an explicitly registered formula crop; never publish or execute."""
import argparse
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_update import build_source_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.domain.source_evidence import require_sha256
from src.ingestion.formula_ocr import CachedFormulaEngine
from src.application.formula_context import attach_reviewed_context
from src.evaluation.formulas import evaluate_formula_display


def _require_box(box):
    if (not isinstance(box, list) or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]):
        raise ValueError('invalid page-point region')


def render_region(path, anchor):
    import numpy as np
    import pymupdf
    with pymupdf.open(path) as document:
        page = document[anchor['physical_page'] - 1]
        box, context = anchor['bbox'], anchor['context_bbox']
        for region in (box, context):
            if region[2] > page.rect.width or region[3] > page.rect.height:
                raise ValueError('region exceeds original page')
        pixmap = page.get_pixmap(dpi=200, clip=pymupdf.Rect(box), alpha=False, colorspace=pymupdf.csRGB)
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        # Literal native text is an unverified candidate, not inferred definitions.
        text = page.get_text('text', clip=pymupdf.Rect(context))
        return image, text, context


def recognize(root, anchor_id, *, engine=None, renderer=render_region):
    gold_path = root / 'data/registry/formula-gold.json'
    gold_bytes_hash = file_digest(gold_path)
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    anchors = gold['anchors']
    ids = [a['anchor_id'] for a in anchors]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate formula anchor IDs')
    anchor = next((a for a in anchors if a['anchor_id'] == anchor_id), None)
    if anchor is None or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', anchor_id):
        raise ValueError('unknown/invalid formula anchor')
    digest, physical_page = anchor['file_sha256'], anchor['physical_page']
    require_sha256(digest)
    if type(physical_page) is not int or physical_page < 1:
        raise ValueError('invalid physical page')
    if anchor.get('coordinate_system') != 'page_points_top_left':
        raise ValueError('unsupported region coordinate system')
    _require_box(anchor['bbox'])
    _require_box(anchor['context_bbox'])
    asset = next((a for a in build_source_catalog(root).assets if a.sha256 == digest), None)
    if asset is None or physical_page > asset.page_count:
        raise ValueError('document/page not registered as primary source')
    path = registered_pdf_path(root, asset.canonical_rel_path)
    if file_digest(path) != digest:
        raise ValueError('PDF SHA-256 mismatch')
    started = time.perf_counter()
    result = {'schema_version': '1', 'anchor_id': anchor_id, 'file_sha256': digest,
              'physical_page': physical_page, 'bbox': None, 'requested_bbox': anchor['bbox'],
              'coordinate_system': 'page_points_top_left',
              'formula_number': anchor.get('formula_number'), 'gold_sha256': gold_bytes_hash,
              'processing_profile': 'targeted-formula-crop-v1:200dpi',
              'raw_latex': None, 'normalized_expression': None, 'executable_expression': None,
              'variable_definitions': [], 'units': [], 'conditions': [],
              'context_evidence': None, 'status': 'source_page_only',
              'publishable': False, 'can_use_for_calculation': False,
              'missing_context': ['verified_variables', 'verified_units', 'verified_conditions'],
              'gold_check': evaluate_formula_display(None, anchor['expected_latex'])}
    try:
        image, text, context_box = renderer(path, anchor)
        result['bbox'] = anchor['bbox']
        result['context_evidence'] = {'physical_page': physical_page, 'bbox': context_box,
                                     'text': text, 'basis': 'native_text_unverified',
                                     'review_status': 'pending_review'}
        backend = engine or CachedFormulaEngine()
        result['recognizer'] = {'name': backend.name, 'version': backend.version}
        latex = backend.predict(image)
        if not isinstance(latex, str) or not latex.strip() or len(latex) > 10000:
            raise ValueError('missing/oversized formula transcription')
        result['raw_latex'] = latex
        result['status'] = 'pending_review'
        result['gold_check'] = evaluate_formula_display(latex, anchor['expected_latex'])
    except Exception as exc:
        result['failure'] = type(exc).__name__
        result['status'] = 'source_page_only'
        result['raw_latex'] = None
    if file_digest(path) != digest or file_digest(gold_path) != gold_bytes_hash:
        raise ValueError('source or gold changed during recognition')
    review_path = root / 'data/registry/formula-context-reviews.json'
    if review_path.is_file():
        review_digest = file_digest(review_path)
        reviews = json.loads(review_path.read_text(encoding='utf-8'))
        attach_reviewed_context(result, reviews['records'])
        if file_digest(review_path) != review_digest:
            raise ValueError('context review changed during recognition')
        result['context_review_sha256'] = review_digest
    result['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    return result


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--anchor-id', required=True)
    cli.add_argument('--model-dir', type=Path, help='Explicit local PP-FormulaNet_plus-M weights; no automatic download')
    args = cli.parse_args()
    result = recognize(ROOT, args.anchor_id, engine=CachedFormulaEngine(args.model_dir))
    folder = ROOT / 'data/model_runtime/formula_regions'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{args.anchor_id}-v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
