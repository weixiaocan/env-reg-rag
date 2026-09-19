"""Read-only audit of all primary pages, including unprocessed and missed areas."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.application.corpus_update import build_source_catalog
from src.application.unified_pdf import write_json
from src.evaluation.unified_pdf_audit import audit_documents


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--canonical', type=Path)
    cli.add_argument('--output', type=Path, default=ROOT / 'data/model_runtime/unified_pdf/audit.json')
    args = cli.parse_args()
    catalog = build_source_catalog(ROOT)
    if args.canonical:
        documents = [json.loads(line) for line in args.canonical.read_text(encoding='utf-8').split('\n') if line.strip()]
    else:
        documents = []
        for asset in catalog.assets:
            pages = []
            for number in range(1, asset.page_count + 1):
                folder = ROOT / 'data/model_runtime/unified_pdf' / asset.sha256 / str(number)
                paths = sorted(folder.glob('page-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
                if paths:
                    pages.append(json.loads(paths[0].read_text(encoding='utf-8')))
            documents.append({'sha256': asset.sha256, 'pages': pages})
    result = audit_documents(ROOT, catalog, documents)
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k not in {'integrity_issues', 'unresolved_items'}}, ensure_ascii=False))
    print(json.dumps({'integrity_issue_count': len(result['integrity_issues']),
                      'unresolved_item_count': len(result['unresolved_items'])}))


if __name__ == '__main__':
    main()
