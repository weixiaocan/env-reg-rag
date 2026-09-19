"""Read-only diagnostic previews, strictly separate from the answering corpus."""
import json
import re
from pathlib import Path
from src.adapters.inventory_document_catalog import InventoryDocumentCatalog
from src.application.pdf_source_audit import file_digest
from src.application.formula_context import attach_reviewed_context


def _json(path):
    if path.stat().st_size > 1000000:
        raise ValueError('oversized preview record')
    return json.loads(path.read_text(encoding='utf-8'))


class FormulaPreviewService:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.folder = self.root / 'data/model_runtime/formula_regions'
        self.gold = self.root / 'data/registry/formula-gold.json'
        self.documents = InventoryDocumentCatalog(project_root=self.root,
            inventory_path=self.root / 'data/registry/inventory.csv')

    def list(self):
        if not self.gold.is_file():
            return []
        anchors = _json(self.gold)['anchors']
        ids = [a['anchor_id'] for a in anchors]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate preview anchors')
        return [{'anchor_id': a['anchor_id'], 'formula_number': a['formula_number'],
                 'physical_page': a['physical_page']} for a in anchors
                if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', a['anchor_id'])]

    def get(self, anchor_id):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', anchor_id) or not self.gold.is_file():
            return None
        anchors = _json(self.gold)['anchors']
        matches = [a for a in anchors if a['anchor_id'] == anchor_id]
        if len(matches) != 1:
            return None
        anchor = matches[0]
        digest, page = anchor['file_sha256'], anchor['physical_page']
        if not re.fullmatch(r'[0-9a-f]{64}', digest) or type(page) is not int or page < 1:
            raise ValueError('invalid preview source identity')
        document_id = 'doc_' + digest[:16]
        document = self.documents.get(document_id)
        result = {'anchor_id': anchor_id, 'file_sha256': digest, 'physical_page': page,
                  'formula_number': anchor['formula_number'],
                  'processing_profile': 'targeted-formula-crop-v1:200dpi',
                  'raw_latex': None, 'status': 'source_page_only', 'publishable': False,
                  'can_use_for_calculation': False, 'variable_definitions': [], 'units': [], 'conditions': [],
                  'missing_context': ['verified_variables', 'verified_units', 'verified_conditions'],
                  'file_name': document.file_name if document else '未登记文档',
                  'source_url': None, 'preview_reason': 'recognition_unavailable'}
        try:
            valid_source = document is not None and document.local_path is not None and file_digest(document.local_path) == digest
        except OSError:
            valid_source = False
        if not valid_source:
            result['preview_reason'] = 'source_unavailable_or_changed'
            return result
        result['source_url'] = f'/api/v1/documents/{document_id}/content#page={page}'
        report_path = self.folder / f'{anchor_id}-v1.json'
        try:
            # Never follow a generated report symlink outside its allowlisted directory.
            if (not self.folder.resolve().is_relative_to(self.root)
                    or not report_path.resolve().is_relative_to(self.folder.resolve())):
                return result
            report = _json(report_path)
            keys = ('anchor_id', 'file_sha256', 'physical_page', 'formula_number', 'processing_profile')
            if (report.get('status') != 'pending_review'
                    or type(report.get('physical_page')) is not int
                    or not all(report.get(k) == result[k] for k in keys)
                    or report.get('gold_sha256') != file_digest(self.gold)):
                result['preview_reason'] = 'recognition_stale'
                return result
            latex = report.get('raw_latex')
            if not isinstance(latex, str) or not latex.strip() or len(latex) > 10000:
                return result
            result.update(raw_latex=latex, status='pending_review', preview_reason='diagnostic_only')
            result['context_evidence'] = {'bbox': anchor['context_bbox']}
            review_path = self.root / 'data/registry/formula-context-reviews.json'
            if review_path.is_file():
                attach_reviewed_context(result, _json(review_path)['records'])
            # Internal matching coordinates and profiles are not browser payloads.
            result.pop('context_evidence', None)
            return result
        except (OSError, ValueError, KeyError, TypeError):
            result.update(raw_latex=None, status='source_page_only', preview_reason='recognition_unavailable',
                          variable_definitions=[], units=[], conditions=[])
            result.pop('context_evidence', None)
            return result
