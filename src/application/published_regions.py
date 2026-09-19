"""Read-only region originals from a hash-validated published corpus snapshot."""
import hashlib
import json
import re
from pathlib import Path

import pymupdf

from src.application.pdf_source_audit import file_digest, registered_pdf_path


class PublishedRegionService:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.regions = {}
        self.version = None
        current = self.root / 'data/registry/corpus-current.json'
        if not current.is_file():
            return
        pointer = json.loads(current.read_text(encoding='utf-8'))
        manifest_path = (self.root / pointer['manifest']).resolve()
        if not manifest_path.is_relative_to(self.root / 'data/registry'):
            raise ValueError('published manifest path outside registry')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        self.version = pointer['corpus_version']
        if manifest['corpus_version'] != self.version:
            raise ValueError('published region version mismatch')
        canonical = (self.root / manifest['canonical_documents']).resolve()
        if not canonical.is_relative_to(self.root / 'data/canonical') or file_digest(canonical) != manifest['canonical_documents_sha256']:
            raise ValueError('published canonical identity mismatch')
        for line in canonical.read_text(encoding='utf-8').split('\n'):
            if not line.strip():
                continue
            document = json.loads(line)
            source = registered_pdf_path(self.root, document['canonical_rel_path'])
            for page in document['pages']:
                for region in page.get('regions', []):
                    if region['region_id'] in self.regions:
                        raise ValueError('duplicate published region')
                    self.regions[region['region_id']] = (region, source, document['sha256'])

    def get(self, region_id):
        if not re.fullmatch(r'up-[0-9a-f]{24}', region_id):
            return None
        record = self.regions.get(region_id)
        if record is None or file_digest(record[1]) != record[2]:
            return None
        region, _, _ = record
        item = {k: region.get(k) for k in ('region_id', 'file_sha256', 'file_name', 'document_version_id',
            'physical_page', 'kind', 'bbox', 'crop_bbox', 'quality_status', 'execution_status', 'issues',
            'text', 'context', 'relations', 'tables', 'parameter_reading', 'reviewed_parameters',
            'reviewed_conditions', 'parameter_missing_symbols', 'processing_config_id',
            'formula_number_candidates', 'purpose_candidates', 'math_category')}
        item.update(corpus_version=self.version, usage_policy='source_locator_only',
                    image_url=f'/api/v1/regions/{region_id}/image')
        return item

    def image(self, region_id):
        item = self.get(region_id)
        if item is None or not item.get('crop_bbox'):
            return None
        _, source, sha = self.regions[region_id]
        with pymupdf.open(source) as pdf:
            page = pdf[item['physical_page'] - 1]
            box = pymupdf.Rect(item['crop_bbox'])
            if not page.rect.contains(box) or box.is_empty or box.width * box.height * (200/72)**2 > 12000000:
                raise ValueError('invalid published source crop')
            page.get_pixmap(dpi=200, alpha=False, colorspace=pymupdf.csRGB)
            png = page.get_pixmap(dpi=200, clip=box, alpha=False).tobytes('png')
        if file_digest(source) != sha:
            return None
        return png
