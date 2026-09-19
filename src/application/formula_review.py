"""Allowlisted, paged review previews. No approval or calculation pathway."""
from collections import Counter
import json
import csv
import math
import hashlib
from pathlib import Path
import re
from src.adapters.inventory_document_catalog import InventoryDocumentCatalog
from src.application.pdf_source_audit import file_digest
from src.evaluation.formula_triage import classify_math_region
from src.application.formula_preview import FormulaPreviewService, _json
from src.application.formula_source_context import source_context
from src.application.formula_parameter_reading import parameter_reading
from src.application.formula_parameter_reviews import attach_parameter_review


class FormulaReviewService:
    def __init__(self, root, run_id):
        if not re.fullmatch('[0-9a-f]{16}', run_id):
            raise ValueError('invalid review run')
        self.root = Path(root).resolve()
        self.path = self.root / 'data/model_runtime/corpus_formulas' / run_id / 'triage.json'
        self.documents = InventoryDocumentCatalog(project_root=self.root,
            inventory_path=self.root / 'data/registry/inventory.csv')
        with (self.root / 'data/registry/inventory.csv').open(encoding='utf-8-sig', newline='') as stream:
            self.page_counts = {r['sha256']: int(r['pages']) for r in csv.DictReader(stream) if r.get('pages')}

    def _items(self):
        if (not self.path.resolve().is_relative_to(self.root / 'data/model_runtime')
                or self.path.is_symlink()):
            raise ValueError('review path escape')
        if self.path.stat().st_size > 5000000:
            raise ValueError('oversized review report')
        report = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(report, dict) or report.get('review_status') != 'pending_review' or not isinstance(report.get('regions'), list):
            raise ValueError('invalid review status')
        items = []
        ids = set()
        for region in report['regions']:
            if not isinstance(region, dict):
                raise ValueError('invalid review region')
            sha, page, identity = region['sha256'], region['physical_page'], region['candidate_id']
            if (not isinstance(sha, str) or not isinstance(identity, str)
                    or not re.fullmatch('[0-9a-f]{64}', sha) or type(page) is not int
                    or not 1 <= page <= self.page_counts.get(sha, 0)
                    or not re.fullmatch(f'layout-{sha[:16]}-{page}-[0-9]+', identity) or identity in ids):
                raise ValueError('invalid/duplicate review identity')
            ids.add(identity)
            document = self.documents.get('doc_' + sha[:16])
            if document is None:
                raise ValueError('unregistered review source')
            items.append({'candidate_id': identity, 'file_sha256': sha, 'physical_page': page,
                          'file_name': document.file_name, 'raw_latex': region.get('raw_latex'),
                          'bbox': region.get('bbox'), **classify_math_region(region.get('raw_latex'))})
        return items

    def list(self, category='all', offset=0, limit=20, query=''):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('invalid search query')
        if category not in {'all', 'relation_candidate', 'expression_candidate', 'fragment_candidate', 'needs_review'}:
            raise ValueError('invalid category')
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 50:
            raise ValueError('invalid pagination')
        items = self._items()
        counts = dict(Counter(r['category'] for r in items))
        filtered = [r for r in items if category == 'all' or r['category'] == category]
        terms = query.casefold().split()
        if terms:
            report = None
            reviews = self._parameter_reviews()
            path = self.path.with_name('quality-context.json')
            try:
                if not path.is_symlink() and path.resolve().is_relative_to(self.path.parent.resolve()) and path.stat().st_size <= 15000000:
                    report = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
            matched = []
            for item in filtered:
                candidate = {**item, 'source_context': []}
                if report is not None:
                    self._canonical_context(candidate, report=report)
                self._attach_parameters(candidate, reviews)
                text = ' '.join([item['file_name'], item['raw_latex'] or '',
                    candidate.get('formula_title', ''),
                    *[e['text'] for c in candidate['source_context']
                      if c['physical_page'] == item['physical_page'] for e in c['excerpts']]]).casefold()
                if all(term in text for term in terms):
                    matched.append(item)
            filtered = matched
        return {'total': len(filtered), 'counts': counts, 'offset': offset, 'limit': limit,
                'diagnostic_only': True, 'items': [{k: r[k] for k in
                    ('candidate_id', 'file_name', 'physical_page', 'category')} for r in filtered[offset:offset + limit]]}

    def get(self, identity):
        if not re.fullmatch('layout-[0-9a-f]{16}-[1-9][0-9]*-[0-9]+', identity):
            return None
        item = next((r for r in self._items() if r['candidate_id'] == identity), None)
        if item is None:
            return None
        document = self.documents.get('doc_' + item['file_sha256'][:16])
        try:
            valid = document.local_path is not None and file_digest(document.local_path) == item['file_sha256']
        except OSError:
            valid = False
        item.update(source_url=None, image_url=None, context_image_url=None, source_context=[],
                    reviewed_parameters=[], reviewed_conditions=[], parameter_missing_symbols=[], parameter_status='not_verified',
                    coordinate_system='page_points_top_left', status='source_page_only')
        box = item['bbox']
        if not (isinstance(box, list) and len(box) == 4
                and all(type(v) in (int, float) and math.isfinite(v) for v in box)
                and 0 <= box[0] < box[2] and 0 <= box[1] < box[3]):
            item['bbox'] = None
        if valid:
            item['source_url'] = f'/api/v1/documents/doc_{item["file_sha256"][:16]}/content#page={item["physical_page"]}'
            if item['bbox'] is not None:
                item['image_url'] = f'/api/v1/formula-review/{identity}/image'
                item['context_image_url'] = item['image_url'] + '?context=true'
            if 'missing_or_oversized_transcription' not in item['flags']:
                item['status'] = 'pending_review'
            else:
                item['raw_latex'] = None
        else:
            item.update(raw_latex=None, bbox=None)
        if valid and item['raw_latex'] and item['bbox']:
            self._visual_findings(item)
            self._source_correction(item)
            self._parameters(item)
            self._attach_parameters(item, self._parameter_reviews())
        if valid and item['bbox']:
            # A text layer is only a source reading aid, not OCR validation.
            import pymupdf
            try:
                with pymupdf.open(document.local_path) as pdf:
                    item['source_context'] = source_context(pdf, item['physical_page'], item['bbox'],
                                                           'doc_' + item['file_sha256'][:16])
                if file_digest(document.local_path) != item['file_sha256']:
                    raise ValueError('source changed during context extraction')
            except pymupdf.FileDataError:
                item['source_context'] = []
            self._canonical_context(item)
            following = [r['bbox'][1] for r in self._items()
                         if r['file_sha256'] == item['file_sha256']
                         and r['category'] == 'relation_candidate'
                         and r['physical_page'] == item['physical_page']
                         and isinstance(r['bbox'], list) and len(r['bbox']) == 4
                         and r['bbox'][1] > item['bbox'][3]]
            item['parameter_reading'] = parameter_reading(item['source_context'], item['physical_page'],
                item['bbox'], min(following) if following else None)
        return item

    def _parameter_reviews(self):
        path = self.root / 'data/registry/formula-parameter-reviews.json'
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root / 'data/registry') or path.stat().st_size > 1500000:
                return None
            report = json.loads(path.read_text(encoding='utf-8'))
            return report if report.get('run_id') == self.path.parent.name else None
        except (OSError, ValueError, AttributeError):
            return None

    def _attach_parameters(self, item, report):
        if attach_parameter_review(item, report, file_digest(self.path), self.page_counts[item['file_sha256']]):
            records = [r for r in report['records'] if r['candidate_id'] == item['candidate_id']]
            for key, target in [('title', 'formula_title'), ('formula_number', 'formula_number')]:
                value = records[0].get(key)
                if isinstance(value, str) and 0 < len(value) <= 200:
                    item[target] = value

    def _visual_findings(self, item):
        path = self.root / 'data/registry/formula-visual-findings.json'
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root / 'data/registry') or path.stat().st_size > 1500000:
                return
            report = json.loads(path.read_text(encoding='utf-8'))
            if (report.get('schema_version') != '1' or report.get('run_id') != self.path.parent.name
                    or report.get('triage_sha256') != file_digest(self.path)):
                return
            matches = [r for r in report['records'] if r.get('candidate_id') == item['candidate_id']]
            if len(matches) != 1:
                return
            r = matches[0]
            notes = r.get('notes')
            if isinstance(notes, list) and len(notes) <= 20 and all(isinstance(n, str) for n in notes):
                notes = '；'.join(notes)
            if (r.get('file_sha256') != item['file_sha256'] or r.get('physical_page') != item['physical_page']
                    or r.get('bbox') != item['bbox']
                    or r.get('transcription_sha256') != hashlib.sha256(item['raw_latex'].encode()).hexdigest()
                    or r.get('screen_status') != 'original_crop_screened'
                    or r.get('finding') not in ('suspected_transcription_or_crop_issue', 'context_or_fragment_only', 'no_obvious_issue_in_crop')
                    or not isinstance(notes, str) or len(notes) > 2000):
                return
            item['visual_screening'] = {'finding': r['finding'], 'notes': notes,
                'basis': 'assistant_visual_original_crop_screening_not_expert_approval',
                'verified': False, 'publishable': False, 'can_use_for_calculation': False}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return

    def _source_correction(self, item):
        path = self.root / 'data/registry/formula-corrections.json'
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root / 'data/registry') or path.stat().st_size > 1000000:
                return
            report = json.loads(path.read_text(encoding='utf-8'))
            if (report.get('schema_version') != '1' or report.get('run_id') != self.path.parent.name
                    or report.get('triage_sha256') != file_digest(self.path)):
                return
            dispositions = [r for r in report.get('dispositions', []) if r.get('candidate_id') == item['candidate_id']]
            if len(dispositions) > 1:
                return
            if dispositions:
                d = dispositions[0]
                if (d.get('file_sha256') != item['file_sha256'] or d.get('physical_page') != item['physical_page']
                        or d.get('bbox') != item['bbox']
                        or d.get('original_transcription_sha256') != hashlib.sha256(item['raw_latex'].encode()).hexdigest()
                        or d.get('status') not in ('classification_rule_issue', 'source_text_not_standalone_formula')
                        or d.get('basis') != 'original_pdf_full_width_line'):
                    return
                item['review_disposition'] = {'status': d['status'], 'basis': d['basis'],
                    'verified_for_calculation': False, 'publishable': False, 'can_use_for_calculation': False}
            matches = [r for r in report['records'] if r.get('candidate_id') == item['candidate_id']]
            if dispositions and matches:
                item.pop('review_disposition', None)
                return
            if len(matches) != 1:
                return
            r = matches[0]; latex = r.get('corrected_latex')
            if (r.get('file_sha256') != item['file_sha256'] or r.get('physical_page') != item['physical_page']
                    or r.get('bbox') != item['bbox']
                    or r.get('original_transcription_sha256') != hashlib.sha256(item['raw_latex'].encode()).hexdigest()
                    or r.get('status') != 'source_image_transcribed_pending_expert'
                    or r.get('basis') not in ('original_pdf_crop', 'original_pdf_full_width_line')
                    or not isinstance(latex, str) or not latex.strip() or len(latex) > 5000
                    or re.search(r'\\(?:input|include|includegraphics|write|openout|openin|read|href|url|def|newcommand|catcode|csname|special|html[A-Za-z]*)(?![A-Za-z])', latex)):
                return
            item['corrected_reading'] = {'latex': latex, 'status': r['status'],
                'basis': r['basis'], 'verified_for_calculation': False,
                'publishable': False, 'can_use_for_calculation': False}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return

    def _canonical_context(self, item, report=None):
        path = self.path.with_name('quality-context.json')
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.path.parent.resolve()) or path.stat().st_size > 15000000:
                return
            if report is None:
                report = json.loads(path.read_text(encoding='utf-8'))
            if report.get('status') != 'pending_review' or report.get('triage_sha256') != file_digest(self.path):
                return
            matches = [r for r in report['records'] if r.get('candidate_id') == item['candidate_id']]
            if len(matches) != 1:
                return
            record = matches[0]
            if (record.get('file_sha256') != item['file_sha256'] or record.get('physical_page') != item['physical_page']
                    or record.get('transcription_sha256') != hashlib.sha256((item['raw_latex'] or '').encode()).hexdigest()):
                return
            contexts = record['source_context']
            if not isinstance(contexts, list) or not 1 <= len(contexts) <= 3:
                return
            clean = []
            seen = set()
            for context in contexts:
                number = context['physical_page']
                if type(number) is not int or number in seen or abs(number-item['physical_page']) > 1 or not 1 <= number <= self.page_counts[item['file_sha256']]:
                    return
                seen.add(number)
                excerpts = context['excerpts']
                if not isinstance(excerpts, list) or len(excerpts) > 60:
                    return
                lines = []
                for excerpt in excerpts:
                    text, box = excerpt['text'], excerpt['bbox']
                    if (not isinstance(text, str) or not text.strip() or len(text) > 4000
                            or not isinstance(box, list) or len(box) != 4
                            or not all(type(v) in (int,float) and math.isfinite(v) for v in box)
                            or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]):
                        return
                    lines.append({'text': text, 'bbox': box, 'physical_page': number, 'verified': False})
                clean.append({'physical_page': number, 'relation': 'current' if number == item['physical_page'] else 'previous' if number < item['physical_page'] else 'next',
                              'status': 'unverified_canonical_text' if lines else 'no_canonical_text_in_window',
                              'page_quality': context.get('page_quality') if context.get('page_quality') in ('approved','quarantine','quarantined','failed') else 'unknown',
                              'extraction_route': context.get('extraction_route') if context.get('extraction_route') in ('full_ocr','native_text','mixed','native') else 'unknown',
                              'verified': False, 'excerpts': lines})
            if item['physical_page'] not in seen:
                return
            item['source_context'] = clean
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Missing/stale diagnostics never erase the original-page fallback.
            return

    def _parameters(self, item):
        gold = self.root / 'data/registry/formula-gold.json'
        if not gold.is_file():
            return
        try:
            anchors = _json(gold)['anchors']
            matches = []
            for anchor in anchors:
                if anchor['file_sha256'] != item['file_sha256'] or anchor['physical_page'] != item['physical_page']:
                    continue
                a, b = anchor['bbox'], item['bbox']
                if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                    matches.append(anchor)
            if len(matches) != 1:
                return
            preview = FormulaPreviewService(self.root).get(matches[0]['anchor_id'])
            # Bind independent existing source reviews, never gold definitions.
            if preview and preview['raw_latex'] == item['raw_latex'] and preview['variable_definitions']:
                item['reviewed_parameters'] = preview['variable_definitions']
                item['reviewed_conditions'] = preview['conditions']
                item['parameter_missing_symbols'] = preview.get('context_review', {}).get('missing_symbols', [])
                item['parameter_status'] = 'matched_existing_source_review'
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def image(self, identity, context=False, full_page=False, page_offset=0):
        import pymupdf
        if type(page_offset) is not int or page_offset not in (-1, 0, 1) or (page_offset and not full_page):
            raise ValueError('invalid source page offset')
        item = self.get(identity)
        if item is None or item['image_url'] is None:
            return None
        document = self.documents.get('doc_' + item['file_sha256'][:16])
        with pymupdf.open(document.local_path) as pdf:
            index = item['physical_page'] - 1 + page_offset
            if not 0 <= index < len(pdf):
                return None
            page = pdf[index]
            box = item['bbox']
            if not full_page and (box[2] > page.rect.width or box[3] > page.rect.height):
                raise ValueError('crop outside original page')
            # Original horizontal page strip, not retypeset model transcription.
            clip = page.rect if full_page else pymupdf.Rect(0, max(0, box[1] - (100 if context else 4)), page.rect.width,
                                min(page.rect.height, box[3] + (240 if context else 4)))
            if clip.width * clip.height * 4 > 12000000:
                raise ValueError('oversized original-page crop')
            image = page.get_pixmap(dpi=144, clip=clip, alpha=False).tobytes('png')
        if file_digest(document.local_path) != item['file_sha256']:
            raise ValueError('source changed during rendering')
        return image
