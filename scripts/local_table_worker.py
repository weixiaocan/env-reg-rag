"""Private local stdio worker; accepts registered PDF paths, never network input."""
import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.application.pdf_source_audit import registered_pdf_path
from src.ingestion.ocr_pdf import OcrPdfParser, PaddleTableRegionEngine
from src.ingestion.local_table_process import PREFIX
from src.ingestion.table_regions import normalize_tables
from src.ingestion.table_context import enrich_table_context


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--data-root', type=Path, default=ROOT)
    data_root = arguments.parse_args().data_root.resolve()
    parser = OcrPdfParser(dpi=200, table_recognition=True)
    region_parser = OcrPdfParser(engine=PaddleTableRegionEngine(), dpi=200)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            path = registered_pdf_path(data_root, request['path'])
            if request.get('regions'):
                tables = []
                for index, box in enumerate(request['regions']):
                    page = region_parser.parse_page(path, physical_page=request['physical_page'], clip_bbox=box)
                    recognized = normalize_tables(page)
                    for table in recognized:
                        table['element_id'] = f'crop-{index}-' + table['element_id']
                        if table.get('cells') and not table.get('bbox'):
                            # This is source geometry, not invented cell geometry.
                            table.update(bbox=list(box), bbox_basis='known_source_table_crop',
                                         source_bounds_basis='validated_original_pdf_crop')
                            table['review_reasons'] = [r for r in table['review_reasons']
                                if r != 'missing_table_region_coordinates']
                            table['review_reasons'].append('cell_geometry_not_recovered_known_crop_only')
                    tables.extend(enrich_table_context(page, recognized))
                result = {'status': 'completed', 'page': tables}
            else:
                page = parser.parse_page(path, physical_page=request['physical_page'])
                result = {'status': 'completed', 'page': page}
        except Exception as exc:
            result = {'status': 'failed', 'reason': type(exc).__name__}
        print(PREFIX + json.dumps(result, ensure_ascii=False, default=lambda v:
            v.tolist() if hasattr(v, 'tolist') else v.item()), flush=True)


if __name__ == '__main__':
    main()
