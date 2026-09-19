"""Read-only cell-quality baseline on the currently published canonical PDFs."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_artifacts import resolve_current_corpus
from src.evaluation.structured_tables import evaluate_cells


def evaluate(root):
    current = resolve_current_corpus(root)
    manifest = json.loads(current.manifest_path.read_text(encoding='utf-8'))
    path = root / manifest['canonical_documents']
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['canonical_documents_sha256']:
        raise ValueError('canonical document SHA-256 mismatch')
    gold_path = root / 'data/registry/table-cell-gold.json'
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    if gold.get('schema_version') != '1' or not gold.get('anchors'):
        raise ValueError('missing or unsupported gold anchors')
    targets = {a['file_sha256'] for a in gold['anchors']}
    documents = {}
    with path.open(encoding='utf-8') as handle:
        for line in handle:
            document = json.loads(line)
            if document['sha256'] in targets:
                documents[document['sha256']] = document
    results = []
    for anchor in gold['anchors']:
        document = documents.get(anchor['file_sha256'])
        if document is None:
            raise ValueError('gold source is absent from published corpus')
        page = next((p for p in document['pages']
                     if p['physical_page'] == anchor['physical_page']), None)
        if page is None:
            raise ValueError('gold physical page is absent')
        tables = page.get('tables', [])
        index = anchor['table_index']
        cells = tables[index].get('cells', []) if 0 <= index < len(tables) else []
        results.append({'anchor_id': anchor['anchor_id'],
                        'file_sha256': anchor['file_sha256'],
                        'physical_page': anchor['physical_page'],
                        'table_index': index, 'structured_table_count': len(tables),
                        **evaluate_cells(cells, anchor['expected_cells'])})
    return {'corpus_version': current.corpus_version,
            'gold_sha256': hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            'status': 'diagnostic_only', 'anchors': results}


if __name__ == '__main__':
    print(json.dumps(evaluate(ROOT), ensure_ascii=False, indent=2))
