"""Conservative version candidates and explicitly reviewed primary-text selection."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from itertools import combinations
import json
from pathlib import Path
import re

from src.application.pdf_source_audit import stable_digest, registered_pdf_path, file_digest
from src.domain.source_evidence import require_sha256

REGISTRY = 'data/registry/document_relations.json'
PROFILE = 'document-relations-v1'


def compare_native_text(root, left, right, *, max_characters=50000):
    """Bounded diagnostic, not semantic equivalence, OCR or approval."""
    import pymupdf

    if type(max_characters) is not int or not 1 <= max_characters <= 50000:
        raise ValueError('native comparison limit must be between 1 and 50000')
    documents = []
    for asset in (left, right):
        path = registered_pdf_path(root, asset.canonical_rel_path)
        if file_digest(path) != asset.sha256:
            raise ValueError('comparison content changed since inventory registration')
        pieces, pages, missing, remaining, truncated = [], [], [], max_characters, False
        try:
            with pymupdf.open(path) as pdf:
                if pdf.needs_pass:
                    raise ValueError('encrypted comparison document')
                for index, page in enumerate(pdf):
                    text = re.sub(r'\s+', '', page.get_text('text'))
                    pages.append({'physical_page': index + 1, 'text_sha256': stable_digest(text)})
                    if not text:
                        missing.append(index + 1)
                    pieces.append(text[:remaining])
                    if len(text) > remaining:
                        truncated = True
                    remaining = max(0, remaining - len(text))
        except Exception as exc:
            raise ValueError('native PDF comparison failed') from exc
        if file_digest(path) != asset.sha256:
            raise ValueError('comparison file changed while reading')
        documents.append({'file_sha256': asset.sha256, 'pages': pages,
                          'missing_native_text_pages': missing, 'truncated': truncated,
                          'text': ''.join(pieces)})
    text_a, text_b = (d.pop('text') for d in documents)
    # SequenceMatcher can be quadratic on adversarial documents; use bounded shingles instead.
    shingles_a = {text_a[i:i + 5] for i in range(max(0, len(text_a) - 4))}
    shingles_b = {text_b[i:i + 5] for i in range(max(0, len(text_b) - 4))}
    union = shingles_a | shingles_b
    covered = not any(d['missing_native_text_pages'] or d['truncated'] for d in documents)
    return {'documents': documents, 'normalized_native_text_equal': bool(text_a) and text_a == text_b if covered else None,
            'character_5gram_jaccard': len(shingles_a & shingles_b) / len(union) if union and covered else None,
            'status': 'diagnostic_only', 'can_confirm_same_version': False,
            'limitation': 'Native text omits scan images, formulas and layout; no automatic equivalence decision.'}


def _standard(value):
    return re.sub(r'\s+', '', value or '').upper().replace('—', '-').replace('–', '-')


def candidates(assets):
    """Identity hints only; neither similarity nor a matching number approves merging."""
    result = []
    for left, right in combinations(sorted(assets, key=lambda a: a.sha256), 2):
        a, b = left.metadata, right.metadata
        standard = _standard(a.get('standard_number'))
        if (standard and re.search(r'-\d{4}$', standard)
                and standard == _standard(b.get('standard_number'))):
            reason = 'same_standard_number_with_year'
        elif (not standard and not b.get('standard_number')
              and left.display_file_name == right.display_file_name
              and a.get('source_authority') and a.get('source_authority') == b.get('source_authority')
              and a.get('publication_date') and a.get('publication_date') == b.get('publication_date')
              and a.get('document_kind') not in {None, '', 'unknown'}
              and a.get('document_kind') == b.get('document_kind')):
            reason = 'same_title_authority_date_kind'
        else:
            continue
        result.append({'member_sha256': [left.sha256, right.sha256], 'reason': reason,
                       'status': 'candidate', 'requires': 'manual_full_document_comparison'})
    return result


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', value):
        raise ValueError('logical identities require stable safe identifiers')


def _official(asset):
    statuses = json.loads(asset.metadata.get('source_field_statuses', '{}'))
    return (asset.metadata.get('source_review') == 'official_fulltext_verified'
            and asset.metadata.get('official_source_uri')
            and all(statuses.get(f) == 'verified' for f in ('source_review', 'official_source_uri')))


def apply_relations(root: Path, assets):
    path = root / REGISTRY
    payload = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'schema_version': '1', 'relations': []}
    if not isinstance(payload, dict) or payload.get('schema_version') != '1' or not isinstance(payload.get('relations'), list):
        raise ValueError('unsupported document relation registry')
    by_hash = {a.sha256: a for a in assets}
    selected = dict(by_hash)
    mappings, decisions, occupied, version_ids = [], [], set(), set()
    for relation in sorted(payload['relations'], key=lambda r: str(r.get('document_version_id', '')) if isinstance(r, dict) else ''):
        if not isinstance(relation, dict) or relation.get('status') not in {'candidate', 'confirmed', 'conflicting'}:
            raise ValueError('unsupported relation state')
        members = relation.get('member_sha256')
        if not isinstance(members, list) or len(members) < 2:
            raise ValueError('relation requires at least two content hashes')
        for digest in members:
            require_sha256(digest)
        if len(set(members)) != len(members):
            raise ValueError('exact duplicates belong in the file inventory, not version relations')
        # A stale confirmed decision must not silently approve a changed document.
        if relation['status'] != 'confirmed':
            decisions.append(relation)
            continue
        if any(h not in by_hash for h in members) or occupied.intersection(members):
            raise ValueError('confirmed relation is stale or overlaps another relation')
        for key in ('document_id', 'document_version_id'):
            _identifier(relation.get(key))
        if relation['document_version_id'] in version_ids:
            raise ValueError('logical version has multiple primary decisions')
        identity = relation.get('identity')
        if not isinstance(identity, dict) or not all(isinstance(identity.get(f), str) and identity[f].strip()
                                                   and identity[f] != 'unknown'
                                                   for f in ('document_kind', 'source_authority', 'revision')):
            raise ValueError('confirmation requires document kind, authority and revision')
        standard = _standard(identity.get('standard_number'))
        if identity['document_kind'] == 'standard':
            if not standard or not re.search(r'-\d{4}$', standard):
                raise ValueError('standard identity must include a version year')
        elif not all(isinstance(identity.get(f), str) and identity[f].strip() for f in ('title', 'publication_date')):
            raise ValueError('non-standard identity requires title and publication date')
        for digest in members:
            metadata = by_hash[digest].metadata
            member_standard = _standard(metadata.get('standard_number'))
            if member_standard and member_standard != standard:
                raise ValueError('different standard versions cannot be merged')
            statuses = json.loads(metadata.get('source_field_statuses', '{}'))
            for field in ('standard_number', 'document_kind', 'source_authority', 'publication_date'):
                if statuses.get(field) == 'conflicting':
                    raise ValueError('resolve conflicting identity evidence before confirmation')
                if statuses.get(field) == 'verified' and metadata.get(field):
                    actual, claimed = metadata[field], identity.get(field)
                    if (_standard(actual) != standard if field == 'standard_number' else actual != claimed):
                        raise ValueError('confirmed identity contradicts verified source evidence')
        comparison = relation.get('comparison')
        if (not isinstance(comparison, dict)
                or comparison.get('method') != 'manual_full_document_comparison'
                or comparison.get('result') not in {'layout_only', 'identical_body'}
                or not isinstance(comparison.get('note'), str) or not comparison['note'].strip()
                or not isinstance(comparison.get('compared_sha256'), list)
                or sorted(comparison['compared_sha256']) != sorted(members)):
            raise ValueError('confirmation requires a full comparison bound to every content hash')
        try:
            checked = datetime.fromisoformat(relation.get('checked_at', '').replace('Z', '+00:00'))
            if checked.tzinfo is None:
                raise ValueError('missing timezone')
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('confirmation requires an actual ISO check time with timezone') from exc
        primary = relation.get('primary_sha256')
        if primary not in members:
            raise ValueError('primary file must be a confirmed member')
        official = sorted(h for h in members if _official(by_hash[h]))
        if official and primary not in official:
            primary = official[0]
        decision = dict(relation, selected_primary_sha256=primary,
                        selection_reason='verified_official' if official else 'explicit_review')
        decisions.append(decision)
        main = by_hash[primary]
        metadata = dict(main.metadata, logical_document_id=relation['document_id'],
                        logical_document_version_id=relation['document_version_id'],
                        document_relation_sha256=stable_digest(decision),
                        primary_selection_reason=decision['selection_reason'])
        # Do not merge approval/source metadata or page coordinates from a different hash.
        selected[primary] = replace(main, metadata=metadata)
        for digest in sorted(members):
            if digest != primary:
                selected.pop(digest)
            mappings.append({'file_sha256': digest, 'legacy_document_version_id': by_hash[digest].document_version_id,
                             'document_id': relation['document_id'], 'logical_document_version_id': relation['document_version_id'],
                             'primary_sha256': primary, 'primary_document_version_id': main.document_version_id,
                             'source_files': list(by_hash[digest].source_files),
                             'page_mapping_status': 'identity' if digest == primary else 'not_established'})
        occupied.update(members)
        version_ids.add(relation['document_version_id'])
    return tuple(selected[h] for h in sorted(selected)), {'profile': PROFILE,
           'decisions': decisions, 'file_mappings': sorted(mappings, key=lambda m: m['file_sha256'])}
