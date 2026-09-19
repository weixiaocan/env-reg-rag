"""Local four-type page orchestration with independently resumable stages.

Execution records are not content approval. Only original caption/reference text
is used for image retrieval; no remote model is called by this module.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import re
from pathlib import Path

import numpy as np
import pymupdf

from src.application.corpus_update import page_extractor_config_hash
from src.application.pdf_source_audit import file_digest, registered_pdf_path
from src.ingestion.formula_layout import CachedLayoutEngine
from src.ingestion.formula_ocr import CachedFormulaEngine
from src.ingestion.local_table_process import LocalTableProcess
from src.ingestion.table_regions import (normalize_tables, parse_table_cells,
                                         recover_tables_from_ocr_elements)
from src.ingestion.table_context import enrich_table_context
from src.application.formula_parameter_reading import parameter_reading
from src.application.formula_parameter_reviews import attach_parameter_review
from src.evaluation.formula_triage import classify_math_region


TYPES = ('text', 'table', 'formula', 'image')
PROFILE = 'unified-local-v5:200dpi:original-context:no-generated-description'
STAGE_PROFILES = {'text': 'native-ocr-routing-v2:rotated-native-coordinates', 'layout': 'all-layout-boxes-v1:200dpi',
                  'tables': 'known-table-region-mobile-v5:200dpi:padding2:ocr-geometry-fallback', 'formula': 'formula-v1:200dpi:padding2'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda v:
                         v.tolist() if hasattr(v, 'tolist') else v.item()) + '\n', encoding='utf-8')
    temporary.replace(path)


def cached_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def valid_detection_box(coords, width, height):
    return (isinstance(coords, (list, tuple)) and len(coords) == 4 and
        all(type(v) in (int, float) and math.isfinite(v) for v in coords) and
        0 <= coords[0] < coords[2] <= width and 0 <= coords[1] < coords[3] <= height)


def local_model_config(layout, formula):
    home_models = Path.home() / '.paddlex/official_models'
    directories = {'layout': layout.model_dir, 'formula': formula.model_dir}
    for name in ('PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec', 'PP-OCRv5_server_det', 'PP-OCRv5_server_rec',
                 'PP-LCNet_x1_0_table_cls', 'SLANet_plus', 'SLANeXt_wired',
                 'RT-DETR-L_wired_table_cell_det', 'RT-DETR-L_wireless_table_cell_det'):
        directories[name] = home_models / name
    return {
        'packages': {name: importlib.metadata.version(name) for name in
                     ('paddleocr', 'paddlex', 'paddlepaddle', 'PyMuPDF')},
        'runtime': {'device': 'cpu', 'cpu_threads': 2, 'mkldnn': False},
        'models': {name: {f: file_digest(directory / f) if (directory / f).is_file() else 'missing'
                          for f in ('inference.json', 'inference.pdiparams', 'inference.yml')}
                   for name, directory in directories.items()},
    }


class UnifiedPageExtractor:
    def __init__(self, root, text_extractor, *, layout=None, formula=None, tables=None,
                 model_config=None):
        self.root = Path(root).resolve()
        self.text_extractor = text_extractor
        self.layout = layout or CachedLayoutEngine()
        self.formula = formula or CachedFormulaEngine()
        self.tables = tables or LocalTableProcess(self.root)
        self.model_config = model_config if model_config is not None else local_model_config(self.layout, self.formula)
        reviews = self.root / 'data/registry/formula-parameter-reviews.json'
        self.config_id = PROFILE + ':' + fingerprint({'text': text_extractor.config_id,
            'models': self.model_config, 'coordinate_profile': 'rotated-native-v1',
            'stage_profiles': STAGE_PROFILES,
            'derived_asset_profile': 'png-original-crop-render-v1',
            'parameter_reviews_sha256': file_digest(reviews) if reviews.is_file() else None})
        self.requires_structure = True

    def _stage(self, directory, name, config, operation, *, timeout_fallback=None):
        stage_profile = STAGE_PROFILES[name.split('-')[0]]
        key = fingerprint({'profile': stage_profile, 'stage': name, 'config': config})
        target = directory / (name + '-' + key[:16] + '.json')
        prior_timeout = False
        if not target.is_file() and name == 'tables':
            old = {}
            old_target = None
            old_key = None
            for previous_profile in ('known-table-region-mobile-v4:200dpi:padding2:literal-inequality-html',
                                     'known-table-region-mobile-v3:200dpi:padding2'):
                candidate_key = fingerprint({'profile': previous_profile, 'stage': name, 'config': config})
                candidate_target = directory / (name + '-' + candidate_key[:16] + '.json')
                candidate = cached_json(candidate_target)
                if candidate.get('config_hash') == candidate_key:
                    old, old_target, old_key = candidate, candidate_target, candidate_key
                    break
            if old.get('execution_status') == 'failed' and old.get('reason') == 'TimeoutError':
                prior_timeout = True
            if (old.get('config_hash') == old_key and old.get('execution_status') == 'completed'
                    and old.get('result_sha256') == fingerprint(old.get('result'))):
                tables = copy.deepcopy(old.get('result') or [])
                compatible = bool(tables)
                for table in tables if old_target.name.startswith('tables-') else ():
                    try:
                        cells = (parse_table_cells(table.get('html', ''))
                                 if table.get('html') else copy.deepcopy(table.get('cells', [])))
                    except ValueError:
                        compatible = False
                        break
                    prior = table.get('cells', [])
                    identity_fields = ('row', 'col', 'row_span', 'col_span')
                    if (not cells or len(cells) != len(prior) or any(
                            any(a.get(k, 1 if k.endswith('_span') else None) !=
                                b.get(k, 1 if k.endswith('_span') else None) for k in identity_fields)
                            for a, b in zip(cells, prior))):
                        compatible = False
                        break
                    for cell, previous in zip(cells, prior):
                        cell['bbox'] = previous.get('bbox')
                    table['cells'] = cells
                if compatible:
                    write_json(target, {**old, 'config_hash': key, 'configuration': config,
                        'result': tables, 'result_sha256': fingerprint(tables),
                        'normalization_reused_from': {'checkpoint': old_target.relative_to(self.root).as_posix(),
                                                     'result_sha256': old['result_sha256']}})
        if not target.is_file() and name in {'layout', 'text'}:
            for previous in ('unified-local-v2:200dpi:original-context:no-generated-description',
                             'unified-local-v1:200dpi:original-context:no-generated-description'):
                old_key = fingerprint({'profile': previous, 'stage': name, 'config': config})
                old_target = directory / (name + '-' + old_key[:16] + '.json')
                if old_target.is_file():
                    old = cached_json(old_target)
                    if (old.get('config_hash') == old_key and old.get('execution_status') == 'completed'
                            and old.get('result_sha256') == fingerprint(old.get('result'))
                            and (name != 'text' or self._text_cache_compatible(old.get('result', {})))):
                        write_json(target, {**old, 'config_hash': key})
                        break
        if target.is_file():
            stored = cached_json(target)
            table_complete = name != 'tables' or (bool(stored.get('result')) and all(
                any(t.get('cells') and t.get('bbox') and pymupdf.Rect(t['bbox']).intersects(pymupdf.Rect(box))
                    for t in stored['result']) for box in config.get('expected_boxes', [])))
            if (stored.get('config_hash') == key and stored.get('execution_status') == 'completed'
                    and stored.get('result_sha256') == fingerprint(stored.get('result')) and table_complete):
                if stored.get('configuration') != config:
                    stored['configuration'] = config
                    write_json(target, stored)
                return stored
        if prior_timeout and timeout_fallback is not None:
            operation = timeout_fallback
        try:
            value = json.loads(json.dumps(operation(), default=lambda v:
                               v.tolist() if hasattr(v, 'tolist') else v.item()))
            result = {'execution_status': 'completed', 'result': value, 'result_sha256': fingerprint(value)}
            if name == 'text' and value.get('decision_status') == 'failed':
                result.update(execution_status='failed', reason='text_page_failed')
        except Exception as exc:
            result = {'execution_status': 'failed', 'reason': type(exc).__name__, 'result': None}
            if hasattr(exc, 'reason_code'):
                result['reason_code'] = exc.reason_code
                result['native_exit_code'] = exc.exit_code
        result['config_hash'] = key
        result['configuration'] = config
        write_json(target, result)
        return result

    def _model_stage_config(self, stage):
        if 'models' not in self.model_config:
            return self.model_config
        model_keys = ({'layout'} if stage == 'layout' else {'formula'} if stage == 'formula' else
                      {'PP-OCRv5_mobile_det', 'PP-OCRv5_mobile_rec'} if stage == 'text' else
                      set(self.model_config['models']) - {'formula', 'layout'})
        return {**{k: v for k, v in self.model_config.items() if k != 'models'},
                'models': {k: self.model_config['models'][k] for k in sorted(model_keys)}}

    def _existing_formula(self, asset, page, bbox, crop_bbox):
        if 'models' not in self.model_config:
            return None
        for target in sorted((self.root / 'data/model_runtime/corpus_formulas').glob(f'*/{asset.sha256}-{page}.json')):
            record = cached_json(target)
            result = record.get('result', {})
            if (record.get('status') != 'completed' or record.get('sha256') != asset.sha256
                    or record.get('physical_page') != page
                    or not str(record.get('config_hash', '')).startswith(target.parent.name)
                    or result.get('sha256') != asset.sha256
                    or result.get('profile') != 'layout-formula-v1:200dpi:score0.5'):
                continue
            packages = result.get('package_versions', {})
            if any(packages.get(k) != self.model_config['packages'].get(k) for k in packages):
                continue
            checksums = result.get('model_checksums', {})
            if (checksums.get(self.layout.name) != self.model_config['models']['layout'] or
                    checksums.get(self.formula.name) != self.model_config['models']['formula']):
                continue
            matches = [r for p in result.get('pages', []) if p.get('physical_page') == page
                       for r in p.get('regions', []) if r.get('physical_page') == page and r.get('sha256') == asset.sha256
                       and isinstance(r.get('raw_latex'), str) and r['raw_latex'].strip()
                       and len(r.get('bbox', [])) == 4 and len(r.get('crop_bbox', [])) == 4
                       and all(abs(a-b) < .0001 for a, b in zip(r['bbox'], bbox))
                       and all(abs(a-b) < .0001 for a, b in zip(r['crop_bbox'], crop_bbox))]
            if len(matches) == 1:
                return {**matches[0], 'checkpoint_ref': target.relative_to(self.root).as_posix(),
                        'checkpoint_sha256': file_digest(target)}
        return None

    def _reviewed_parameters(self, region, existing, page_count):
        if existing is None:
            return
        parameter_path = self.root / 'data/registry/formula-parameter-reviews.json'
        triage_path = self.root / Path(existing['checkpoint_ref']).parent / 'triage.json'
        if not parameter_path.is_file() or not triage_path.is_file():
            return
        review = json.loads(parameter_path.read_text(encoding='utf-8'))
        item = {'candidate_id': existing['candidate_id'], 'file_sha256': region['file_sha256'],
                'physical_page': region['physical_page'], 'bbox': existing['bbox'], 'raw_latex': region['raw_latex']}
        if attach_parameter_review(item, review, file_digest(triage_path), page_count):
            region['parameter_source_candidate_id'] = existing['candidate_id']
            for key in ('reviewed_parameters', 'reviewed_conditions', 'parameter_missing_symbols', 'parameter_status'):
                region[key] = item[key]

    def _text(self, asset, physical_page):
        # Reuse only the exact text configuration, never its structure-coverage claim.
        key = page_extractor_config_hash(self.text_extractor)
        cached = self.root / 'data/canonical/pages' / asset.sha256 / key[:12] / f'{physical_page:04d}.json'
        if cached.is_file():
            page = cached_json(cached)
            page.pop('checkpoint_sha256', None)
            if (page.get('physical_page') == physical_page and page.get('decision_status') != 'failed'
                    and self._text_cache_compatible(page)):
                if page.get('extraction_route') == 'native':
                    return page
        asset_cache = self.root / 'data/canonical/assets' / f'{asset.sha256}.{key[:12]}.json'
        if asset_cache.is_file():
            payload = cached_json(asset_cache)
            if payload.get('sha256') == asset.sha256 and payload.get('config_hash') == key:
                matches = [p for p in payload.get('pages', []) if p.get('physical_page') == physical_page
                           and p.get('decision_status') != 'failed' and self._text_cache_compatible(p)]
                if len(matches) == 1:
                    if matches[0].get('extraction_route') == 'native':
                        return matches[0]
        return self.text_extractor.extract(asset, physical_page=physical_page)

    @staticmethod
    def _text_cache_compatible(page):
        return (page.get('extraction_route') != 'native' or not page.get('rotation')
                or any(n.get('kind') == 'pdf_rotation_matrix' for n in page.get('coordinate_normalizations', [])))

    @staticmethod
    def _kind(label):
        if label == 'formula':
            return 'formula'
        if label == 'table':
            return 'table'
        if label in {'image', 'figure', 'chart', 'header_image', 'footer_image', 'seal'}:
            return 'image'
        if label in {'text', 'paragraph_title', 'doc_title', 'title', 'abstract',
                     'header', 'footer', 'number', 'footnote', 'reference',
                     'figure_title', 'table_title', 'formula_number', 'vision_footnote', 'content', 'aside_text',
                     'figure_table_chart_title', 'algorithm', 'reference_content'}:
            return 'text'
        return 'unsupported'

    @staticmethod
    def _context(page, bbox):
        return [{'text': str(e.get('text') or ''), 'bbox': e['bbox'],
                 'physical_page': page['physical_page'], 'element_id': e['element_id']}
                for e in page.get('elements', []) if e.get('bbox') and str(e.get('text') or '').strip()
                and e['bbox'][3] >= bbox[1] - 100 and e['bbox'][1] <= bbox[3] + 240]

    def _text_stage_config(self):
        return {'extractor': self.text_extractor.config_id, 'models': self._model_stage_config('text')}

    def _predict_layout(self, image):
        boxes = self.layout.predict(image)
        if not isinstance(boxes, list) or any(not isinstance(b, dict) for b in boxes):
            raise ValueError('invalid local layout output')
        return boxes

    def prepare_layout_text(self, asset, *, physical_page):
        """Warm cheap page stages before slow structure stages in the same entry."""
        path = registered_pdf_path(self.root, asset.canonical_rel_path)
        if file_digest(path) != asset.sha256:
            raise ValueError('source checksum mismatch')
        directory = self.root / 'data/model_runtime/unified_pdf' / asset.sha256 / str(physical_page)
        with pymupdf.open(path) as pdf:
            pix = pdf[physical_page-1].get_pixmap(dpi=200, alpha=False, colorspace=pymupdf.csRGB)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            layout = self._stage(directory, 'layout', self._model_stage_config('layout'), lambda: self._predict_layout(image))
            text = self._stage(directory, 'text', self._text_stage_config(), lambda: self._text(asset, physical_page))
        if file_digest(path) != asset.sha256:
            raise ValueError('source changed during preparation')
        return {'file_sha256': asset.sha256, 'physical_page': physical_page,
                'layout_status': layout['execution_status'], 'text_status': text['execution_status']}

    def extract(self, asset, *, physical_page):
        path = registered_pdf_path(self.root, asset.canonical_rel_path)
        if file_digest(path) != asset.sha256:
            raise ValueError('source checksum mismatch')
        directory = self.root / 'data/model_runtime/unified_pdf' / asset.sha256 / str(physical_page)
        with pymupdf.open(path) as doc:
            original = doc[physical_page - 1]
            pix = original.get_pixmap(dpi=200, alpha=False, colorspace=pymupdf.csRGB)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            layout = self._stage(directory, 'layout', self._model_stage_config('layout'),
                                 lambda: self._predict_layout(image))
            text = self._stage(directory, 'text', self._text_stage_config(),
                               lambda: self._text(asset, physical_page))
            page = copy.deepcopy(text['result']) if text['result'] else {
                'physical_page': physical_page, 'page_index': physical_page - 1,
                'width': original.rect.width, 'height': original.rect.height, 'rotation': original.rotation,
                'decision_status': 'failed', 'decision_reasons': [text['reason']], 'publishable': False,
                'text': '', 'elements': [], 'tables': [], 'extraction_route': 'failed',
                'parser_name': 'unified-local', 'parser_profile': PROFILE,
                'coordinate_normalizations': []}
            page.update(layout_status=layout['execution_status'], regions=[],
                        layout_config_hash=layout['config_hash'], processing_profile=PROFILE)
            if layout['execution_status'] != 'completed':
                page['layout_failure'] = layout['reason']
            raw_boxes = layout['result'] or []
            page['layout_boxes'] = raw_boxes
            table_stage = None
            if any(b.get('label') == 'table' for b in raw_boxes):
                def recognize_tables():
                    if isinstance(self.tables, LocalTableProcess):
                        boxes = []
                        for b in raw_boxes:
                            if b.get('label') != 'table' or not valid_detection_box(b.get('coordinate'), pix.width, pix.height):
                                continue
                            c = b['coordinate']
                            rect = pymupdf.Rect(c[0]*original.rect.width/pix.width, c[1]*original.rect.height/pix.height,
                                               c[2]*original.rect.width/pix.width, c[3]*original.rect.height/pix.height)
                            boxes.append(list((rect + (-2, -2, 2, 2)) & original.rect))
                        if not boxes:
                            raise ValueError('no valid local table regions')
                        try:
                            recognized = self.tables.recognize_regions(path, physical_page=physical_page, regions=boxes)
                            if not recognized or not all(any(t.get('cells') and t.get('bbox')
                                    and pymupdf.Rect(t['bbox']).intersects(pymupdf.Rect(box))
                                    for t in recognized) for box in boxes):
                                raise ValueError('table_region_structure_not_recovered')
                        except TimeoutError as exc:
                            recognized = recover_tables_from_ocr_elements(
                                page, boxes, failure_reason=type(exc).__name__)
                            for table in recognized:
                                table['processing_attempts'] = [
                                    {'route': 'cropped_table', 'execution_status': 'failed',
                                     'reason': getattr(exc, 'reason_code', type(exc).__name__),
                                     'native_exit_code': getattr(exc, 'exit_code', None)},
                                    {'route': 'ocr_geometry_fallback', 'execution_status': 'completed'}]
                        except (RuntimeError, ValueError) as exc:
                            try:
                                recognized = normalize_tables(self.tables.parse_page(path, physical_page=physical_page))
                                fallback_route = 'full_page_fallback'
                            except (RuntimeError, TimeoutError, ValueError) as fallback_exc:
                                recognized = recover_tables_from_ocr_elements(
                                    page, boxes, failure_reason=type(fallback_exc).__name__)
                                fallback_route = 'ocr_geometry_fallback'
                            for table in recognized:
                                table['processing_attempts'] = [
                                    {'route': 'cropped_table', 'execution_status': 'failed',
                                     'reason': getattr(exc, 'reason_code', type(exc).__name__),
                                     'native_exit_code': getattr(exc, 'exit_code', None)},
                                    {'route': fallback_route, 'execution_status': 'completed'}]
                        return enrich_table_context(page, recognized)
                    return normalize_tables(self.tables.parse_page(path, physical_page=physical_page))
                expected_boxes = [[b['coordinate'][0]*original.rect.width/pix.width,
                                   b['coordinate'][1]*original.rect.height/pix.height,
                                   b['coordinate'][2]*original.rect.width/pix.width,
                                   b['coordinate'][3]*original.rect.height/pix.height]
                                  for b in raw_boxes if b.get('label') == 'table'
                                  and valid_detection_box(b.get('coordinate'), pix.width, pix.height)]
                table_stage = self._stage(directory, 'tables', {
                    'models': self._model_stage_config('tables'), 'layout_config_hash': layout['config_hash'],
                    'expected_boxes': expected_boxes, 'original_context_hash': text.get('result_sha256')},
                    recognize_tables,
                    timeout_fallback=lambda: recover_tables_from_ocr_elements(
                        page, expected_boxes, failure_reason='repeated_local_table_model_timeout'))
            for index, raw in enumerate(raw_boxes):
                kind = self._kind(raw.get('label'))
                coords = raw.get('coordinate')
                identity = fingerprint({'sha256': asset.sha256, 'page': physical_page,
                                        'index': index, 'box': raw, 'profile': 'source-layout-region-v1'})
                region = {'region_id': 'up-' + identity[:24], 'element_id': 'up-' + identity[:24],
                          'file_sha256': asset.sha256, 'file_name': asset.display_file_name,
                          'document_version_id': asset.document_version_id, 'physical_page': physical_page,
                          'kind': kind, 'model_label': raw.get('label'), 'reading_order': index,
                          'execution_status': 'completed', 'quality_status': 'needs_review',
                          'issues': [], 'publishable': False, 'relations': []}
                region['processing_config_id'] = self.config_id
                page['regions'].append(region)
                if not isinstance(coords, (list, tuple)) or len(coords) != 4 or any(
                        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in coords
                        ) or not (0 <= coords[0] < coords[2] <= pix.width and 0 <= coords[1] < coords[3] <= pix.height):
                    region.update(execution_status='unsupported', bbox=None, issues=['invalid_detection_coordinates'])
                    continue
                box = [coords[0] * original.rect.width / pix.width, coords[1] * original.rect.height / pix.height,
                       coords[2] * original.rect.width / pix.width, coords[3] * original.rect.height / pix.height]
                region.update(bbox=box, coordinate_system='page_points_top_left')
                recognition_crop = (pymupdf.Rect(box) + (-2, -2, 2, 2)) & original.rect
                crop_box = recognition_crop
                if kind == 'formula':
                    display_box = pymupdf.Rect(box)
                    for element in page.get('elements', []):
                        if not element.get('bbox'):
                            continue
                        line = pymupdf.Rect(element['bbox'])
                        if (line.intersects(display_box) and line.height <= max(30, display_box.height * 2)
                                and re.search(r'[=＝∑√]', str(element.get('text') or ''))):
                            display_box |= line
                    crop_box = (display_box + (-5, -5, 5, 5)) & original.rect
                    region['display_crop_basis'] = 'detected_math_plus_intersecting_original_equation_line_with_5pt_margin'
                    region['recognition_crop_bbox'] = list(recognition_crop)
                render_identity = fingerprint({'crop_bbox': list(crop_box), 'dpi': 200,
                    'renderer_version': importlib.metadata.version('PyMuPDF')})[:12]
                image_path = directory / (region['region_id'] + '-' + render_identity + '.png')
                if kind != 'text':
                    original.get_pixmap(dpi=200, clip=crop_box, alpha=False).save(image_path)
                    region.update(image_ref=image_path.relative_to(self.root).as_posix(),
                                  image_sha256=file_digest(image_path), crop_bbox=list(crop_box))
                context = self._context(page, box)
                region['context'] = context
                region['text'] = '\n'.join(e['text'] for e in page.get('elements', []) if e.get('bbox')
                    and pymupdf.Rect(e['bbox']).intersects(pymupdf.Rect(box)))
                if kind == 'formula':
                    existing = self._existing_formula(asset, physical_page, box, list(recognition_crop))
                    def recognize():
                        if existing is not None:
                            return existing['raw_latex']
                        crop = original.get_pixmap(dpi=200, clip=recognition_crop, alpha=False, colorspace=pymupdf.csRGB)
                        arr = np.frombuffer(crop.samples, dtype=np.uint8).reshape(crop.height, crop.width, crop.n)
                        value = self.formula.predict(arr)
                        if not isinstance(value, str) or not value.strip() or len(value) > 10000:
                            raise ValueError('invalid formula output')
                        return value
                    result = self._stage(directory, 'formula-' + identity[:24], self._model_stage_config('formula'), recognize)
                    region.update(execution_status=result['execution_status'], raw_latex=result['result'],
                                  issues=['formula_transcription_and_context_not_verified'])
                    if result.get('reason'):
                        region['issues'].append(result['reason'])
                    if existing is not None:
                        region['recognition_reused_from'] = {k: existing[k] for k in
                            ('candidate_id', 'checkpoint_ref', 'checkpoint_sha256')}
                        self._reviewed_parameters(region, existing, asset.page_count)
                    later = [b['coordinate'][1] * original.rect.height / pix.height for b in raw_boxes
                             if b.get('label') == 'formula' and len(b.get('coordinate', [])) == 4
                             and b['coordinate'][1] * original.rect.height / pix.height > box[3]]
                    region['parameter_reading'] = parameter_reading(
                        [{'physical_page': physical_page, 'excerpts': context}], physical_page,
                        box, min(later) if later else None)
                    region['issues'].extend(['formula_number_not_verified', 'units_not_verified',
                                             'applicability_not_verified', 'cross_page_relation_not_established'])
                    if not region['parameter_reading']['excerpts']:
                        region['issues'].append('explicit_parameter_marker_not_located')
                elif kind == 'table':
                    region['execution_status'] = table_stage['execution_status']
                    matched = [t for t in table_stage['result'] or [] if t.get('bbox')
                               and pymupdf.Rect(t['bbox']).intersects(pymupdf.Rect(box))]
                    region['tables'] = matched
                    region['issues'] = ['table_structure_and_context_not_verified']
                    for table in matched:
                        region['issues'].extend(table['region_quality']['reasons'])
                        if not table.get('cells'):
                            region['execution_status'] = 'failed'
                            region['issues'].append('table_cells_not_recovered')
                        if not table.get('header_rows'):
                            region['issues'].append('table_header_roles_not_recovered')
                        if not table.get('caption_candidates'):
                            region['issues'].append('table_caption_not_located')
                        if not table.get('footnotes'):
                            region['issues'].append('table_footnotes_not_located')
                    region['issues'].append('table_cross_page_relation_not_established')
                    if not matched:
                        region['execution_status'] = 'failed'
                        region['issues'].append(table_stage.get('reason', 'no_matching_structured_table'))
                        if table_stage.get('reason_code'):
                            region['issues'].append(table_stage['reason_code'])
                        if table_stage.get('native_exit_code') is not None:
                            region['issues'].append('native_exit_code:' + str(table_stage['native_exit_code']))
                elif kind == 'image':
                    region['issues'] = ['caption_not_found', 'body_reference_not_established']
                    # A caption must have a literal figure identifier and be uniquely near this figure.
                    candidates = [c for c in context if re.match(r'^\s*图\s*\d+(?:[.－-]\d+)*', c['text'])
                                  and box[3] - 5 <= c['bbox'][1] <= box[3] + 60]
                    nearby_images = [b for b in raw_boxes if self._kind(b.get('label')) == 'image']
                    if len(candidates) == 1 and len(nearby_images) == 1:
                        caption = candidates[0]
                        match = re.match(r'^\s*(图\s*\d+(?:[.－-]\d+)*)', caption['text'])
                        region.update(caption=caption, figure_number=match.group(1))
                        region['issues'] = ['caption_and_reference_require_review']
                        region['relations'].append({'kind': 'caption', 'source': caption, 'verified': False,
                                                    'basis': 'literal_figure_identifier_unique_nearby_caption'})
                    elif candidates:
                        region['issues'].append('caption_association_ambiguous')
                    region['text'] = region.get('caption', {}).get('text', '')
                elif kind == 'unsupported':
                    region.update(execution_status='unsupported', issues=['unsupported_layout_label'])
                else:
                    region['quality_status'] = 'passed' if page['decision_status'] == 'approved' else 'quarantined'
            page['content_types'] = {}
            for kind in TYPES:
                regions = [r for r in page['regions'] if r['kind'] == kind]
                states = [r['execution_status'] for r in regions]
                state = ('failed' if layout['execution_status'] != 'completed' or 'failed' in states else
                         'unsupported' if 'unsupported' in states else 'completed' if regions else 'not_detected')
                if kind == 'text' and text['execution_status'] != 'completed':
                    state = 'failed'
                page['content_types'][kind] = {'execution_status': state, 'detected_regions': len(regions),
                    'quality_status': 'needs_review' if kind != 'text' else page['decision_status']}
            page['structure_execution_complete'] = text['execution_status'] == 'completed' and layout['execution_status'] == 'completed' and all(
                r['execution_status'] == 'completed' for r in page['regions'])
            page['layout_omission_quality'] = 'not_independently_verified'
        if file_digest(path) != asset.sha256:
            raise ValueError('source changed during processing')
        write_json(directory / ('page-' + fingerprint(self.config_id)[:16] + '.json'), page)
        return page


def enrich_original_relations(documents):
    """Link only unique literal figure identities; preserve ambiguous references."""
    for document in documents:
        tables_by_page = {}
        for page in document['pages']:
            formulas = [r for r in page.get('regions', []) if r['kind'] == 'formula' and r.get('bbox')]
            for formula in formulas:
                formula['math_category'] = classify_math_region(formula.get('raw_latex'))['category']
            for region in page.get('regions', []):
                if region['kind'] == 'formula':
                    if not region.get('bbox'):
                        continue
                    next_tops = [r['bbox'][1] for r in formulas if r['math_category'] == 'relation_candidate'
                                 and r['bbox'][1] > region['bbox'][3]]
                    region['parameter_reading'] = parameter_reading(
                        [{'physical_page': page['physical_page'], 'excerpts': region.get('context', [])}],
                        page['physical_page'], region['bbox'], min(next_tops) if next_tops else None)
                    candidates = []
                    for context in region.get('context', []):
                        if not pymupdf.Rect(context['bbox']).intersects(pymupdf.Rect(region['bbox'])):
                            continue
                        for match in re.finditer(r'[（(]\s*(\d+(?:\.\d+){0,4}(?:[-－]\d+)?)\s*[)）]', context['text']):
                            candidates.append({'number': match.group(1), 'source': context,
                                               'character_span': [match.start(), match.end()], 'verified': False})
                    region['formula_number_candidates'] = candidates
                    region['purpose_candidates'] = [c for c in region.get('context', [])
                        if c['bbox'][3] <= region['bbox'][1] + 5 and re.search(r'下列公式|计算公式|按.*计算', c['text'])]
                    if not candidates:
                        region['issues'].append('literal_formula_number_not_located')
                    elif len(candidates) > 1:
                        region['issues'].append('literal_formula_number_association_ambiguous')
                if region['kind'] != 'table':
                    continue
                for table in region.get('tables', []):
                    captions = table.get('caption_candidates', [])
                    if len(captions) != 1 or captions[0].get('association_status') == 'ambiguous':
                        continue
                    text = captions[0]['text']
                    match = re.match(r'^\s*(续\s*)?表\s*(\d+(?:[.－-]\d+)*)', text)
                    if not match:
                        continue
                    table.update(table_number=match.group(2), caption=text,
                                 is_explicit_continuation=bool(match.group(1)))
                    tables_by_page.setdefault(page['physical_page'], []).append((region, table))
        from src.application.formula_context_relations import attach_cross_page_formula_sources
        attach_cross_page_formula_sources(document)
        for number, tables in tables_by_page.items():
            for region, table in tables:
                if not table['is_explicit_continuation']:
                    continue
                previous = [(r, t) for r, t in tables_by_page.get(number-1, [])
                            if t['table_number'] == table['table_number']]
                if len(previous) == 1:
                    region['relations'].append({'kind': 'explicit_continued_table_number',
                        'target_region_id': previous[0][0]['region_id'], 'target_physical_page': number-1,
                        'source': table['caption_candidates'][0], 'verified': False,
                        'basis': 'literal_continuation_caption_same_number_unique_previous_page'})
                else:
                    region['issues'].append('continued_table_predecessor_not_unique_or_missing')
        figures = {}
        for page in document['pages']:
            for region in page.get('regions', []):
                if region['kind'] == 'image' and region.get('figure_number'):
                    token = re.sub(r'\s+', '', region['figure_number'])
                    figures.setdefault(token, []).append(region)
        for token, regions in figures.items():
            if len(regions) != 1:
                for r in regions:
                    r['issues'].append('figure_number_not_unique_in_document')
                continue
            region = regions[0]
            pattern = re.compile(re.escape(token) + r'(?![\d.－-])')
            for page in document['pages']:
                for element in page.get('elements', []):
                    text = str(element.get('text') or '')
                    compact = re.sub(r'\s+', '', text)
                    if pattern.search(compact) and not re.match(r'^\s*图\s*\d', text):
                        region['relations'].append({'kind': 'explicit_body_figure_reference',
                            'source': {'physical_page': page['physical_page'], 'element_id': element['element_id'],
                                       'bbox': element.get('bbox'), 'text': text},
                            'basis': 'unique_literal_figure_number', 'verified': False})
                        region['text'] += '\n' + text
            if not any(r['kind'] == 'explicit_body_figure_reference' for r in region['relations']):
                region['issues'].append('explicit_body_reference_not_found')


_WORKER_EXTRACTOR = None


def warm_unified_preparation(root, asset, physical_page, text_options):
    global _WORKER_EXTRACTOR
    from src.application.corpus_update import AutomaticPageExtractor
    if _WORKER_EXTRACTOR is None:
        _WORKER_EXTRACTOR = UnifiedPageExtractor(root, AutomaticPageExtractor(project_root=root, **text_options))
    return _WORKER_EXTRACTOR.prepare_layout_text(asset, physical_page=physical_page)


def warm_unified_asset(root, asset, text_options):
    """Keep native model runtimes in independent processes, not shared threads."""
    global _WORKER_EXTRACTOR
    from src.application.corpus_update import AutomaticPageExtractor, CorpusUpdateService
    if _WORKER_EXTRACTOR is None:
        _WORKER_EXTRACTOR = UnifiedPageExtractor(root, AutomaticPageExtractor(project_root=root, **text_options))
    extractor = _WORKER_EXTRACTOR
    doc, reused = CorpusUpdateService(root, page_extractor=extractor)._document(
        asset, page_extractor_config_hash(extractor), extractor)
    return {'file_sha256': asset.sha256, 'file_name': asset.display_file_name, 'page_count': len(doc['pages']), 'reused': reused}
