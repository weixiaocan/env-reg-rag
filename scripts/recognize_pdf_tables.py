"""Targeted local table recognition; writes only ignored model-runtime diagnostics."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_update import build_source_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.domain.source_evidence import require_sha256
from src.evaluation.structured_tables import evaluate_cells
from src.ingestion.ocr_pdf import OcrPdfParser
from src.ingestion.table_regions import normalize_tables


def recognize(root, sha256, physical_page, *, parser=None):
    require_sha256(sha256)
    if type(physical_page) is not int or physical_page < 1:
        raise ValueError('physical page must be a positive integer')
    assets = build_source_catalog(root).assets
    asset = next((a for a in assets if a.sha256 == sha256), None)
    if asset is None or physical_page > asset.page_count:
        raise ValueError('selected document/page is not registered')
    path = registered_pdf_path(root, asset.canonical_rel_path)
    if file_digest(path) != sha256:
        raise ValueError('PDF SHA-256 mismatch')
    started = time.perf_counter()
    result = {'schema_version': '1', 'file_sha256': sha256, 'physical_page': physical_page,
              'processing_profile': 'targeted-table-regions-v1:ppstructure:200dpi',
              'publishable': False, 'tables': [], 'gold_checks': []}
    try:
        page = (parser or OcrPdfParser(dpi=200, table_recognition=True)).parse_page(
            path, physical_page=physical_page)
        if page['physical_page'] != physical_page:
            raise ValueError('recognizer returned wrong physical page')
        result['tables'] = normalize_tables(page)
        result['recognizer'] = {k: page['raw'].get(k) for k in
                                ('engine_name', 'engine_version', 'render_dpi')}
        result['status'] = 'pending_review' if any(t['cells'] for t in result['tables']) else 'source_page_only'
    except Exception as exc:
        result['status'] = 'source_page_only'
        result['failure'] = type(exc).__name__
    if file_digest(path) != sha256:
        raise ValueError('PDF changed during recognition')
    gold_path = root / 'data/registry/table-cell-gold.json'
    if gold_path.is_file():
        gold = json.loads(gold_path.read_text(encoding='utf-8'))
        result['gold_sha256'] = file_digest(gold_path)
        for anchor in gold['anchors']:
            if anchor['file_sha256'] != sha256 or anchor['physical_page'] != physical_page:
                continue
            index = anchor['table_index']
            cells = result['tables'][index]['cells'] if 0 <= index < len(result['tables']) else []
            result['gold_checks'].append({'anchor_id': anchor['anchor_id'],
                                         **evaluate_cells(cells, anchor['expected_cells'])})
    result['elapsed_seconds'] = round(time.perf_counter() - started, 3)
    return result


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--page', required=True, type=int)
    args = cli.parse_args()
    result = recognize(ROOT, args.sha256, args.page)
    folder = ROOT / 'data/model_runtime/table_regions'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{args.sha256}-p{args.page}-v1.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
