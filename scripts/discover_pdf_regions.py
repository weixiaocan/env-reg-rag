"""Scan published canonical artifacts, never update or publish a corpus."""
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.application.corpus_artifacts import resolve_current_corpus
from src.ingestion.region_discovery import discover_regions


def scan_published(root):
    root = Path(root).resolve()
    current = resolve_current_corpus(root)
    manifest = json.loads(current.manifest_path.read_text(encoding='utf-8'))
    path = (root / manifest['canonical_documents']).resolve()
    if not path.is_relative_to((root / 'data/canonical').resolve()):
        raise ValueError('canonical path outside allowed directory')
    expected = manifest['canonical_documents_sha256']
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('canonical checksum mismatch')
    with path.open(encoding='utf-8') as stream:
        report = discover_regions(json.loads(line) for line in stream if line.strip())
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('canonical changed during scan')
    if report['page_count'] != manifest['page_count']:
        raise ValueError('canonical page count mismatch')
    if report['document_count'] != manifest['selected_content_count']:
        raise ValueError('canonical document count mismatch')
    report.update(corpus_version=current.corpus_version, canonical_sha256=expected)
    return report


def main():
    report = scan_published(ROOT)
    if not re.fullmatch(r'corpus-[a-zA-Z0-9_-]+', report['corpus_version']):
        raise ValueError('invalid report corpus version')
    directory = ROOT / 'data/model_runtime/region_discovery'
    if not directory.resolve().is_relative_to(ROOT / 'data/model_runtime'):
        raise ValueError('report directory outside runtime storage')
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / (report['corpus_version'] + '.json')
    if output.is_symlink():
        raise ValueError('report target cannot be a symlink')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    from collections import Counter
    print(json.dumps({k: v for k, v in report.items() if k != 'candidates'}, ensure_ascii=False))
    print(json.dumps(dict(Counter(c['kind'] for c in report['candidates'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
