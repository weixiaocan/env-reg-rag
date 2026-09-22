"""Audit every selected source page and region, independently of build success."""
import json
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import pymupdf

from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.evaluation.region_detection import evaluate_formula_locations
from src.evaluation.structured_tables import evaluate_cells
from src.evaluation.table_source_values import cell_source_checks


def fixed_table_checks(root, documents):
    gold = root / 'data/registry/table-cell-gold.json'
    rows = []
    if not gold.is_file():
        return rows
    for anchor in json.loads(gold.read_text(encoding='utf-8'))['anchors']:
        page = next((p for d in documents if d['sha256'] == anchor['file_sha256']
                     for p in d['pages'] if p['physical_page'] == anchor['physical_page']), None)
        tables = [(r, t) for r in (page or {}).get('regions', []) if r['kind'] == 'table'
                  for t in r.get('tables', []) if t.get('bbox')]
        tables.sort(key=lambda pair: (pair[1]['bbox'][1], pair[1]['bbox'][0]))
        index = anchor['table_index']
        region, table = tables[index] if index < len(tables) else ({}, {})
        rows.append({'anchor_id': anchor['anchor_id'], 'file_sha256': anchor['file_sha256'],
            'physical_page': anchor['physical_page'], 'region_id': region.get('region_id'),
            'bbox': table.get('bbox'), 'source_page_processed': bool(page and page.get('layout_status') == 'completed'),
            **evaluate_cells(table.get('cells', []), anchor['expected_cells'])})
    return rows


def original_page_signals(pdf_page, regions):
    """Independent source-layer cues; signals are not semantic content approval."""
    signals = []
    def covered(box, kinds):
        rect = pymupdf.Rect(box)
        return any(r.get('kind') in kinds and r.get('bbox') and
            (rect & pymupdf.Rect(r['bbox'])).get_area() >= .5 * rect.get_area() for r in regions)
    for index, block in enumerate(pdf_page.get_text('dict')['blocks']):
        if block.get('type') != 0:
            continue
        text = '\n'.join(''.join(s['text'] for s in line['spans']) for line in block['lines']).strip()
        if len(re.sub(r'\s', '', text)) < 4:
            continue
        box = list(pymupdf.Rect(block['bbox']) * pdf_page.rotation_matrix)
        reason = None
        if re.match(r'^图\s*\d', text) and not covered(box, {'image', 'text'}):
            reason = 'original_figure_caption_candidate_not_assigned'
        elif re.match(r'^(?:续)?表\s*\d', text) and not covered(box, {'table', 'text'}):
            reason = 'original_table_caption_candidate_not_assigned'
        elif re.search(r'[=＝∑√]', text) and len(text) < 180 and not covered(box, {'formula'}):
            reason = 'original_math_operator_candidate_not_assigned'
        elif not covered(box, {'text', 'table', 'formula', 'image'}):
            reason = 'original_text_block_not_covered_by_layout'
        if reason:
            signals.append({'reason': reason, 'bbox': box, 'source_block_index': index,
                            'original_text': text, 'quality_status': 'needs_review'})
    for index, image in enumerate(pdf_page.get_image_info()):
        box = list(pymupdf.Rect(image['bbox']) * pdf_page.rotation_matrix)
        area = pymupdf.Rect(box).get_area()
        # A scan is a carrier, not a figure. Its semantic recall still needs
        # independent review; enumerate the exact source page instead of only
        # reporting a generic corpus-level limitation.
        if area >= .8 * pdf_page.rect.get_area():
            signals.append({'reason': 'original_full_page_image_layout_semantics_not_independently_verified',
                            'bbox': box, 'source_image_index': index, 'quality_status': 'needs_review'})
        elif area > 200 and not covered(box, {'image', 'table', 'formula'}):
            signals.append({'reason': 'original_embedded_image_candidate_not_assigned', 'bbox': box,
                            'source_image_index': index, 'quality_status': 'needs_review'})
    for index, drawing in enumerate(pdf_page.get_drawings()):
        box = list(pymupdf.Rect(drawing['rect']) * pdf_page.rotation_matrix)
        if pymupdf.Rect(box).get_area() > 200 and not covered(box, {'image', 'table', 'formula'}):
            signals.append({'reason': 'original_vector_graphic_candidate_not_assigned', 'bbox': box,
                            'source_drawing_index': index, 'quality_status': 'needs_review'})
    return signals


def original_page_crop_hashes(source, physical_page, regions):
    """Match the declared full-page-primed MuPDF rendering profile exactly."""
    result = {}
    if not any(r['kind'] != 'text' for r in regions):
        return result
    # Embedded scans can render differently when the first render is a tiny
    # crop. A fresh document and the same full-page priming prevent false
    # mismatches without accepting any pixel or checksum tolerance.
    with pymupdf.open(source) as pdf:
        page = pdf[physical_page-1]
        page.get_pixmap(dpi=200, alpha=False, colorspace=pymupdf.csRGB)
        for region in regions:
            crop = region.get('crop_bbox')
            if (region['kind'] == 'text' or not isinstance(crop, list) or len(crop) != 4 or
                    not all(type(v) in (int, float) and math.isfinite(v) for v in crop)):
                continue
            box = pymupdf.Rect(crop)
            if box.is_empty or not page.rect.contains(box):
                continue
            png = page.get_pixmap(dpi=200, clip=box, alpha=False).tobytes('png')
            result[region.get('region_id')] = hashlib.sha256(png).hexdigest()
    return result


def audit_documents(root, catalog, documents):
    root = Path(root).resolve()
    by_sha = {d['sha256']: d for d in documents}
    issues, gaps, region_counts, execution = [], [], Counter(), Counter()
    candidates = []
    cell_checks = []
    pages_analyzed = 0
    omission_pages, omission_signals = 0, []
    region_ids = set()
    def issue(asset, page, reason, region=None):
        issues.append({'file_name': asset.display_file_name, 'file_sha256': asset.sha256,
                       'physical_page': page, 'region_id': region.get('region_id') if region else None,
                       'bbox': region.get('bbox') if region else None, 'reason': reason})
    for asset in catalog.assets:
        source = registered_pdf_path(root, asset.canonical_rel_path)
        if file_digest(source) != asset.sha256:
            issue(asset, None, 'source_checksum_mismatch')
            continue
        document = by_sha.get(asset.sha256, {})
        raw_pages = document.get('pages', [])
        pages = {p['physical_page']: p for p in raw_pages}
        if len(pages) != len(raw_pages):
            issue(asset, None, 'duplicate_physical_page')
        with pymupdf.open(source) as pdf:
            if len(pdf) != asset.page_count:
                issue(asset, None, 'source_page_count_mismatch')
            for number in range(1, asset.page_count + 1):
                page = pages.get(number)
                if page is None:
                    issue(asset, number, 'page_processing_record_missing')
                    continue
                if page.get('layout_status') != 'completed':
                    issue(asset, number, 'layout_not_completed:' + str(page.get('layout_failure', 'unknown')))
                    continue
                pages_analyzed += 1
                if set(page.get('content_types', {})) != {'text', 'table', 'formula', 'image'}:
                    issue(asset, number, 'four_type_summary_missing')
                regions = page.get('regions', [])
                expected_images = original_page_crop_hashes(source, number, regions)
                omission_pages += 1
                for signal in original_page_signals(pdf[number-1], regions):
                    item = {'file_name': asset.display_file_name, 'file_sha256': asset.sha256,
                            'physical_page': number, 'region_id': None, 'kind': 'original_source_signal', **signal}
                    omission_signals.append(item)
                    gaps.append(item)
                if len(regions) != len(page.get('layout_boxes', [])):
                    issue(asset, number, 'detection_region_accounting_mismatch')
                for r in regions:
                    region_counts[r['kind']] += 1
                    execution[r['execution_status']] += 1
                    identity = r.get('region_id')
                    if not identity or identity in region_ids:
                        issue(asset, number, 'duplicate_or_missing_region_id', r)
                    region_ids.add(identity)
                    if r.get('file_sha256') != asset.sha256 or r.get('physical_page') != number:
                        issue(asset, number, 'region_source_identity_mismatch', r)
                    b = r.get('bbox')
                    rect = pdf[number - 1].rect
                    if (not isinstance(b, list) or len(b) != 4 or
                            not all(type(v) in (int, float) and math.isfinite(v) for v in b) or
                            not 0 <= b[0] < b[2] <= rect.width or not 0 <= b[1] < b[3] <= rect.height):
                        issue(asset, number, 'invalid_region_bbox', r)
                        continue
                    if r['kind'] != 'text':
                        image = (root / r.get('image_ref', '')).resolve()
                        runtime = (root / 'data/model_runtime').resolve()
                        if (not image.is_relative_to(runtime) or not image.is_file() or
                                file_digest(image) != r.get('image_sha256')):
                            issue(asset, number, 'original_region_image_missing_or_changed', r)
                        else:
                            crop = r.get('crop_bbox')
                            if not isinstance(crop, list) or len(crop) != 4 or not rect.contains(pymupdf.Rect(crop)):
                                issue(asset, number, 'invalid_original_crop_bbox', r)
                            else:
                                if expected_images.get(identity) != r['image_sha256']:
                                    issue(asset, number, 'region_image_differs_from_original_pdf_crop', r)
                    if r['execution_status'] != 'completed':
                        issue(asset, number, 'region_execution_' + r['execution_status'], r)
                    reasons = list(r.get('issues', []))
                    if r['kind'] in {'formula', 'table', 'image'}:
                        reasons.append('original_crop_semantic_completeness_not_independently_verified')
                    for reason in reasons:
                        gaps.append({'file_name': asset.display_file_name, 'file_sha256': asset.sha256,
                                     'physical_page': number, 'region_id': identity, 'bbox': b,
                                     'kind': r['kind'], 'reason': reason})
                    if r['kind'] == 'table':
                        for check in cell_source_checks(pdf[number-1], r):
                            item = {'file_name': asset.display_file_name, 'file_sha256': asset.sha256,
                                    'physical_page': number, **check}
                            cell_checks.append(item)
                            if check['status'] != 'literal_cell_text_matches_original_layer_pending_review':
                                gaps.append({**item, 'kind': 'table_cell', 'reason': check['status'],
                                             'table_region_bbox': b})
                    if r['kind'] == 'formula':
                        candidates.append({'candidate_id': identity, 'sha256': asset.sha256,
                                           'physical_page': number, 'kind': 'formula', 'bbox': b})
        if file_digest(source) != asset.sha256:
            issue(asset, None, 'source_changed_during_audit')
    gold = root / 'data/registry/formula-gold.json'
    omission = evaluate_formula_locations(candidates, json.loads(gold.read_text(encoding='utf-8'))['anchors']) if gold.is_file() else None
    if omission is not None:
        anchors = json.loads(gold.read_text(encoding='utf-8'))['anchors']
        covered_pages = {(d['sha256'], p['physical_page']) for d in documents for p in d.get('pages', [])
                         if p.get('layout_status') == 'completed'}
        for row, anchor in zip(omission['anchors'], anchors):
            row['source_page_processed'] = (anchor['file_sha256'], anchor['physical_page']) in covered_pages
            row['assessment_status'] = 'evaluated' if row['source_page_processed'] else 'source_page_not_processed'
        omission['evaluated_anchor_count'] = sum(r['source_page_processed'] for r in omission['anchors'])
    return {'schema_version': '1', 'scope': 'all_selected_primary_pages_and_detected_regions',
            'expected_document_count': len(catalog.assets),
            'expected_page_count': sum(a.page_count for a in catalog.assets),
            'layout_completed_page_count': pages_analyzed,
            'region_counts': dict(region_counts), 'execution_status_counts': dict(execution),
            'integrity_issues': issues,
            'unresolved_items': gaps, 'fixed_formula_omission_check': omission,
            'fixed_table_cell_checks': fixed_table_checks(root, documents),
            'original_table_cell_text_checks': {'checks': cell_checks,
                'status_counts': dict(Counter(c['status'] for c in cell_checks)),
                'limitation': 'Lexical comparison in proposed model cell geometry; source OCR layers and coordinate order remain unverified. No automatic content approval.'},
            'original_page_omission_check': {'checked_page_count': omission_pages,
                'unassigned_source_signals': omission_signals, 'full_recall_established': False,
                'limitation': 'Original text-layer and embedded-image cues; scanned semantics and vector-only diagrams still require independent original-page review.'},
            'full_corpus_content_quality_established': False,
            'artifact_integrity_passed': not issues,
            'note': 'Fixed original-PDF anchors measure missed regions, not full-corpus recall or content accuracy.'}
