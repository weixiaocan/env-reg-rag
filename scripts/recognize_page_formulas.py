"""Detect formulas from complete PDF pages, then recognize detected crops locally."""
import argparse
import json
import importlib.metadata
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.corpus_update import build_source_catalog
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.domain.source_evidence import require_sha256
from src.ingestion.formula_layout import CachedLayoutEngine, formula_regions
from src.ingestion.formula_ocr import CachedFormulaEngine


def recognize_pages(root, sha256, pages, *, layout=None, recognizer=None, asset=None, provenance=None):
    import numpy as np
    import pymupdf
    require_sha256(sha256)
    asset = asset or next((a for a in build_source_catalog(root).assets if a.sha256 == sha256), None)
    if asset is None or asset.sha256 != sha256 or not pages or any(type(p) is not int or not 1 <= p <= asset.page_count for p in pages):
        raise ValueError('invalid primary source/page selection')
    path = registered_pdf_path(root, asset.canonical_rel_path)
    if file_digest(path) != sha256:
        raise ValueError('source checksum mismatch')
    layout, recognizer = layout or CachedLayoutEngine(), recognizer or CachedFormulaEngine()
    report = {'schema_version': '1', 'sha256': sha256, 'profile': 'layout-formula-v1:200dpi:score0.5',
              'layout_model': layout.name, 'formula_model': recognizer.name, 'pages': [],
              'publishable': False, 'can_use_for_calculation': False}
    report['coordinate_system'] = 'page_points_top_left'
    report.update(provenance or model_provenance(layout, recognizer))
    with pymupdf.open(path) as document:
        for physical_page in pages:
            started = time.perf_counter()
            page = document[physical_page - 1]
            pix = page.get_pixmap(dpi=200, alpha=False, colorspace=pymupdf.csRGB)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            regions = formula_regions(layout.predict(image), pix.width, pix.height, page.rect.width, page.rect.height)
            for index, region in enumerate(regions):
                region.update(candidate_id=f'layout-{sha256[:16]}-{physical_page}-{index}', sha256=sha256,
                              physical_page=physical_page, raw_latex=None)
                # Two-point fixed padding is not derived from any gold box.
                box = pymupdf.Rect(region['bbox']) + (-2, -2, 2, 2)
                box &= page.rect
                region['crop_bbox'] = list(box)
                crop = page.get_pixmap(dpi=200, clip=box, alpha=False, colorspace=pymupdf.csRGB)
                crop_image = np.frombuffer(crop.samples, dtype=np.uint8).reshape(crop.height, crop.width, crop.n)
                try:
                    latex = recognizer.predict(crop_image)
                    if not isinstance(latex, str) or not latex.strip() or len(latex) > 10000:
                        raise ValueError('invalid transcription')
                    region['raw_latex'] = latex
                except Exception as exc:
                    region['failure'] = type(exc).__name__
            report['pages'].append({'physical_page': physical_page, 'regions': regions,
                                    'elapsed_seconds': round(time.perf_counter() - started, 3)})
            print(f'page {physical_page}: {len(regions)} formula regions', flush=True)
    if file_digest(path) != sha256:
        raise ValueError('source changed during recognition')
    return report


def model_provenance(layout, recognizer):
    return {'package_versions': {name: importlib.metadata.version(name) for name in ('paddleocr', 'paddlex', 'paddlepaddle')},
            'model_checksums': {name: {filename: file_digest(directory / filename)
                                      for filename in ('inference.json', 'inference.pdiparams', 'inference.yml')}
                                for name, directory in ((layout.name, layout.model_dir),
                                                        (recognizer.name, recognizer.model_dir))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--pages', type=int, nargs='+', required=True)
    args = parser.parse_args()
    report = recognize_pages(ROOT, args.sha256, args.pages)
    folder = ROOT / 'data/model_runtime/page_formulas'
    if not folder.resolve().is_relative_to(ROOT / 'data/model_runtime'):
        raise ValueError('report directory outside runtime storage')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{args.sha256[:16]}-{"-".join(map(str, args.pages))}.json'
    if target.is_symlink():
        raise ValueError('symlink report target')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
