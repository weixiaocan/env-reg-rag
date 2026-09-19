"""Re-scan pinned published artifacts and evaluate only registered formula locations."""
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.discover_pdf_regions import scan_published
from src.evaluation.region_detection import evaluate_formula_locations


def main():
    report = scan_published(ROOT)
    gold_bytes = (ROOT / 'data/registry/formula-gold.json').read_bytes()
    gold = json.loads(gold_bytes)
    result = evaluate_formula_locations(report['candidates'], gold['anchors'])
    result['corpus_version'] = report['corpus_version']
    result['canonical_sha256'] = report['canonical_sha256']
    result['location_gold_sha256'] = hashlib.sha256(gold_bytes).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
