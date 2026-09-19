"""Repeat fixed, visually checked formula retrieval and parameter examples."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.application.formula_review import FormulaReviewService


def evaluate(root, image_checks=True):
    root = Path(root)
    fixture = json.loads((root / 'data/registry/formula-acceptance-cases.json').read_text(encoding='utf-8'))
    service = FormulaReviewService(root, fixture['run_id'])
    results = []
    for case in fixture['cases']:
        listing = service.list(query=case['query'], limit=10)
        item = service.get(case['candidate_id'])
        checks = {'retrieved_in_first_10': case['candidate_id'] in [r['candidate_id'] for r in listing['items']]}
        if item:
            document = service.documents.get('doc_' + case['file_sha256'][:16])
            checks.update(
                source_sha256=hashlib.sha256(document.local_path.read_bytes()).hexdigest() == case['file_sha256'],
                source_page=item['physical_page'] == case['physical_page'],
                formula_number=item.get('formula_number') == case['formula_number'],
                parameters=set(v['symbol'] for v in item['reviewed_parameters']) == set(case['expected_symbols']),
                parameter_pages=sorted(set(v['physical_page'] for v in item['reviewed_parameters'])) == sorted(case['expected_parameter_pages']),
                explicit_missing=set(item['parameter_missing_symbols']) == set(case['expected_missing_symbols']),
                source_link=bool(item['source_url']) and item['source_url'].endswith('#page=' + str(case['physical_page'])),
            )
            if image_checks:
                for name, options in [('formula_crop', {}), ('context_crop', {'context': True})]:
                    image = service.image(case['candidate_id'], **options)
                    checks[name] = bool(image) and image.startswith(b'\x89PNG\r\n\x1a\n')
        else:
            checks['detail_exists'] = False
        results.append({'candidate_id': case['candidate_id'], 'query': case['query'],
                        'checks': checks, 'passed': all(checks.values())})
    return {'scope': 'fixed_visual_samples_only_not_full_corpus_accuracy',
            'sample_count': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-images', action='store_true')
    args = parser.parse_args()
    report = evaluate(ROOT, image_checks=not args.skip_images)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['passed'] == report['sample_count'] else 1)
