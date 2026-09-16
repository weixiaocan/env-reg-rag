"""Read-only duplicate candidates and native-text comparison; never approves a merge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.corpus_update import build_source_catalog
from src.application.document_relations import candidates, compare_native_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', nargs=2, metavar=('SHA256_A', 'SHA256_B'))
    args = parser.parse_args()
    try:
        original = build_source_catalog(ROOT, apply_document_relations=False)
        selected = build_source_catalog(ROOT)
        result = {'source_file_count': original.source_file_count,
                  'unique_content_count': original.unique_content_count,
                  'exact_duplicate_groups': [{'sha256': a.sha256, 'primary_path': a.canonical_rel_path,
                                             'source_files': list(a.source_files)}
                                            for a in original.assets if len(a.source_files) > 1],
                  'candidates': candidates(original.assets),
                  'selected_content_count': len(selected.assets),
                  'document_relations': selected.document_relations}
        if args.compare:
            by_hash = {a.sha256: a for a in original.assets}
            if any(h not in by_hash for h in args.compare) or args.compare[0] == args.compare[1]:
                raise ValueError('compare requires two distinct registered content hashes')
            result['native_comparison'] = compare_native_text(ROOT, *(by_hash[h] for h in args.compare))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
