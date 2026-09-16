"""Plan and build a complete, resumable corpus candidate from registered PDFs."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import threading
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Protocol

from src.evaluation.evidence_builder import build_evidence_units
from src.evaluation.retrieval_chunk_builder import build_retrieval_chunks
from src.ingestion.native_pdf import NativePdfParser
from src.ingestion.ocr_pdf import OcrPdfParser, PaddleTextOcrEngine


SOURCE_REVIEW_RANK = {
    "official_fulltext_verified": 4,
    "official_record_found": 3,
    "needs_primary_source": 2,
    "needs_review": 1,
    "": 0,
}
_CORPUS_ARTIFACT_PROFILE = "corpus-artifacts-v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_json_sha(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _official_path(row: dict[str, str]) -> bool:
    return "/_official_verification/" in f"/{row['rel_path'].replace(chr(92), '/')}"


def _identity_score(row: dict[str, str]) -> tuple[int, int, int, str]:
    return (
        int(bool(row.get("std_no"))),
        int(row.get("document_kind") not in {"", "unknown"}),
        int(not _official_path(row)),
        row.get("file_name", ""),
    )


@dataclass(frozen=True)
class ContentAsset:
    sha256: str
    canonical_rel_path: str
    display_file_name: str
    page_count: int
    source_files: tuple[dict[str, str], ...]
    metadata: dict[str, str]

    @property
    def asset_id(self) -> str:
        return f"asset_{self.sha256[:16]}"

    @property
    def document_version_id(self) -> str:
        return f"doc_{self.sha256[:16]}"


@dataclass(frozen=True)
class SourceCatalog:
    source_file_count: int
    unique_content_count: int
    duplicate_copy_count: int
    assets: tuple[ContentAsset, ...]

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "source_file_count": self.source_file_count,
            "unique_content_count": self.unique_content_count,
            "duplicate_copy_count": self.duplicate_copy_count,
            "assets": [
                {
                    "sha256": asset.sha256,
                    "canonical_rel_path": asset.canonical_rel_path,
                    "display_file_name": asset.display_file_name,
                    "page_count": asset.page_count,
                    "metadata": asset.metadata,
                    "source_files": list(asset.source_files),
                }
                for asset in self.assets
            ],
        }


def build_source_catalog(
    project_root: Path,
    *,
    inventory_path: Path | None = None,
    reviews_path: Path | None = None,
) -> SourceCatalog:
    """Collapse physical duplicates while retaining every source-file record."""

    root = Path(project_root).resolve()
    inventory_rows = _read_csv(
        inventory_path or root / "data" / "registry" / "inventory.csv"
    )
    if not inventory_rows:
        raise ValueError("inventory contains no PDF records")
    reviews = _read_csv(
        reviews_path or root / "data" / "registry" / "document_reviews.csv"
    )
    review_by_name = {row["file_name"]: row for row in reviews}
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in inventory_rows:
        digest = row.get("sha256", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid inventory SHA-256: {row.get('rel_path', '')}")
        path = root / row["rel_path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        if _sha256(path) != digest:
            raise ValueError(f"inventory SHA-256 mismatch: {row['rel_path']}")
        grouped.setdefault(digest, []).append(dict(row))

    assets = []
    for digest, rows in sorted(grouped.items()):
        official_rows = [row for row in rows if _official_path(row)]
        canonical = max(official_rows or rows, key=_identity_score)
        identity = max(rows, key=_identity_score)
        page_counts = {int(row["pages"]) for row in rows}
        if len(page_counts) != 1:
            raise ValueError(f"duplicate files disagree on page count: {digest}")
        related_reviews = [review_by_name[row["file_name"]] for row in rows if row["file_name"] in review_by_name]
        review = max(
            related_reviews,
            key=lambda row: SOURCE_REVIEW_RANK.get(row.get("source_review", ""), 0),
            default={},
        )
        metadata = {
            "standard_number": identity.get("std_no", ""),
            "document_kind": identity.get("document_kind", "unknown") or "unknown",
            "jurisdiction": identity.get("jurisdiction", "unknown") or "unknown",
            "official_source_uri": review.get("official_source_uri", "")
            or identity.get("official_source_uri", ""),
            "source_authority": review.get("source_authority", ""),
            "source_review": review.get("source_review", "")
            or identity.get("source_review", "needs_review")
            or "needs_review",
            "publication_date": review.get("publication_date", ""),
            "effective_from": review.get("effective_date", ""),
            "effective_status": review.get("effective_status", "")
            or identity.get("effective_status", "unknown")
            or "unknown",
            "local_file_match": review.get("local_file_match", ""),
            "selection_status": review.get("selection_status", ""),
        }
        assets.append(
            ContentAsset(
                sha256=digest,
                canonical_rel_path=canonical["rel_path"].replace("\\", "/"),
                display_file_name=identity["file_name"],
                page_count=page_counts.pop(),
                source_files=tuple(sorted(rows, key=lambda row: row["rel_path"])),
                metadata=metadata,
            )
        )
    return SourceCatalog(
        source_file_count=len(inventory_rows),
        unique_content_count=len(assets),
        duplicate_copy_count=len(inventory_rows) - len(assets),
        assets=tuple(assets),
    )


class PageExtractor(Protocol):
    config_id: str

    def extract(self, asset: ContentAsset, *, physical_page: int) -> dict[str, Any]: ...


class _HtmlCells(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.row = -1
        self.col = 0
        self.current: dict[str, Any] | None = None
        self.cells: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row += 1
            self.col = 0
        elif tag in {"td", "th"}:
            values = dict(attrs)
            self.current = {
                "row_span": int(values.get("rowspan") or 1),
                "col_span": int(values.get("colspan") or 1),
                "parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag not in {"td", "th"} or self.current is None:
            return
        text = re.sub(r"\s+", " ", "".join(self.current["parts"])).strip()
        self.cells.append(
            {
                "row": max(self.row, 0),
                "col": self.col,
                "row_span": self.current["row_span"],
                "col_span": self.current["col_span"],
                "text": text,
                "bbox": None,
            }
        )
        self.col += self.current["col_span"]
        self.current = None


class AutomaticPageExtractor:
    """Prefer native text and invoke OCR only when the text layer is insufficient."""

    def __init__(
        self,
        *,
        project_root: Path,
        enable_ocr: bool,
        minimum_native_characters: int = 20,
        table_recognition: bool = False,
        layout_recognition: bool = False,
    ):
        self.project_root = Path(project_root).resolve()
        self.enable_ocr = enable_ocr
        self.minimum_native_characters = minimum_native_characters
        self.table_recognition = table_recognition
        self.layout_recognition = layout_recognition or table_recognition
        self.native = NativePdfParser()
        if not enable_ocr:
            self.ocr = None
        elif self.layout_recognition:
            self.ocr = OcrPdfParser(table_recognition=table_recognition)
        else:
            self.ocr = OcrPdfParser(engine=PaddleTextOcrEngine(), dpi=150)
        self.config_id = (
            f"auto-page-v6:native-min={minimum_native_characters}:ocr={enable_ocr}:"
            f"layout={self.layout_recognition}:tables={table_recognition}"
        )

    @staticmethod
    def _canonical_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for index, table in enumerate(tables):
            parser = _HtmlCells()
            parser.feed(str(table.get("html") or ""))
            parser.close()
            cells = parser.cells
            if not cells and str(table.get("text") or "").strip():
                cells = [
                    {
                        "row": 0,
                        "col": 0,
                        "row_span": 1,
                        "col_span": 1,
                        "text": str(table["text"]).strip(),
                        "bbox": None,
                    }
                ]
            if not cells:
                continue
            converted.append(
                {
                    "element_id": table.get("table_id") or f"table-{index}",
                    "caption": "",
                    "html": table.get("html") or "",
                    "markdown": "",
                    "cells": cells,
                    "unit_context": [],
                    "footnotes": [],
                }
            )
        return converted

    @staticmethod
    def _page(source: dict[str, Any], *, status: str, reasons: list[str]) -> dict[str, Any]:
        return {
            "physical_page": source["physical_page"],
            "page_index": source["page_index"],
            "display_page_label": source.get("display_page_label"),
            "width": source["width"],
            "height": source["height"],
            "rotation": source["rotation"],
            "extraction_route": source["extraction_route"],
            "parser_name": (
                source.get("raw", {}).get("engine_name", "paddleocr")
                if source["extraction_route"] == "full_ocr"
                else "pymupdf-native"
            ),
            "parser_profile": "full-corpus-auto-v1",
            "decision_status": status,
            "decision_reasons": reasons,
            "publishable": status == "approved",
            "text": source.get("text") or "",
            "elements": source.get("elements") or [],
            "tables": AutomaticPageExtractor._canonical_tables(source.get("tables") or []),
            "raw_artifact_ref": "",
            "coordinate_normalizations": [],
        }

    def extract(self, asset: ContentAsset, *, physical_page: int) -> dict[str, Any]:
        path = self.project_root / asset.canonical_rel_path
        native = self.native.parse_page(path, physical_page=physical_page)
        count = native["quality"]["metrics"]["non_whitespace_characters"]
        if native["quality"]["status"] == "pass" and count >= self.minimum_native_characters:
            return self._page(native, status="approved", reasons=[])
        reasons = list(native["quality"]["reasons"])
        if count < self.minimum_native_characters:
            reasons.append("insufficient_native_text")
        if not self.enable_ocr:
            status = "quarantine" if str(native.get("text") or "").strip() else "failed"
            return self._page(native, status=status, reasons=reasons + ["ocr_not_enabled"])
        assert self.ocr is not None
        ocr = self.ocr.parse_page(path, physical_page=physical_page)
        ocr_status = ocr["quality"]["status"]
        reasons = list(ocr["quality"]["reasons"])
        non_white_ratio = float(
            ocr.get("raw", {}).get("non_white_pixel_ratio", 1.0)
        )
        if ocr_status == "fail":
            status = "quarantine"
            reasons = [
                "visual_blank_page"
                if non_white_ratio <= 0.0001
                else "visual_only_page"
            ]
        else:
            status = {
                "pass": "approved",
                "review": "quarantine",
                "fail": "failed",
            }[ocr_status]
        return self._page(ocr, status=status, reasons=reasons)


class CorpusUpdateService:
    """Build immutable corpus artifacts while reusing per-content page caches."""

    def __init__(
        self,
        project_root: Path,
        *,
        page_extractor: PageExtractor,
        progress: Callable[[dict[str, Any]], None] | None = None,
        worker_count: int = 1,
        page_extractor_factory: Callable[[], PageExtractor] | None = None,
    ):
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if worker_count > 1 and page_extractor_factory is None:
            raise ValueError("parallel builds require page_extractor_factory")
        self.root = Path(project_root).resolve()
        self.page_extractor = page_extractor
        self.progress = progress or (lambda event: None)
        self.worker_count = worker_count
        self.page_extractor_factory = page_extractor_factory

    def _cache_path(self, asset: ContentAsset, config_hash: str) -> Path:
        return (
            self.root
            / "data"
            / "canonical"
            / "assets"
            / f"{asset.sha256}.{config_hash[:12]}.json"
        )

    def _page_cache_path(
        self, asset: ContentAsset, config_hash: str, physical_page: int
    ) -> Path:
        return (
            self.root
            / "data"
            / "canonical"
            / "pages"
            / asset.sha256
            / config_hash[:12]
            / f"{physical_page:04d}.json"
        )

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _write_text(self, path: Path, content: str) -> None:
        """Publish a generated text artifact only after it is fully written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _page(
        self,
        asset: ContentAsset,
        config_hash: str,
        physical_page: int,
        extractor: PageExtractor,
    ) -> tuple[dict[str, Any], bool]:
        page_cache = self._page_cache_path(asset, config_hash, physical_page)
        if page_cache.is_file():
            page = json.loads(page_cache.read_text(encoding="utf-8"))
            if page.get("physical_page") != physical_page:
                raise ValueError(f"page cache mismatch: {page_cache}")
            return page, True
        try:
            page = extractor.extract(asset, physical_page=physical_page)
        except Exception as exc:  # keep the public cache free of sensitive details
            page = {
                "physical_page": physical_page,
                "page_index": physical_page - 1,
                "display_page_label": None,
                "width": 1.0,
                "height": 1.0,
                "rotation": 0,
                "extraction_route": "failed",
                "parser_name": type(extractor).__name__,
                "parser_profile": "full-corpus-auto-v1",
                "decision_status": "failed",
                "decision_reasons": [type(exc).__name__],
                "publishable": False,
                "text": "",
                "elements": [],
                "tables": [],
                "raw_artifact_ref": "",
                "coordinate_normalizations": [],
            }
        page["sample_id"] = f"P-{asset.sha256[:8]}-{physical_page:04d}"
        page["raw_artifact_ref"] = self._cache_path(
            asset, config_hash
        ).relative_to(self.root).as_posix()
        self._write_json(page_cache, page)
        return page, False

    def _document(
        self, asset: ContentAsset, config_hash: str, extractor: PageExtractor
    ) -> tuple[dict[str, Any], bool]:
        cache_path = self._cache_path(asset, config_hash)
        payload: dict[str, Any]
        if cache_path.is_file():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                payload.get("sha256") == asset.sha256
                and payload.get("config_hash") == config_hash
                and payload.get("status") == "complete"
                and len(payload.get("pages", [])) == asset.page_count
            ):
                completed_pages = list(payload["pages"])
                was_reused = True
            else:
                completed_pages = []
                was_reused = False
        else:
            payload = {}
            completed_pages = []
            was_reused = False

        if (
            not completed_pages
            and payload.get("sha256") == asset.sha256
            and payload.get("config_hash") == config_hash
            and payload.get("status") == "processing"
        ):
            completed_pages = list(payload.get("pages", []))
        all_pages_reused = True
        for physical_page in range(len(completed_pages) + 1, asset.page_count + 1):
            page, page_reused = self._page(
                asset, config_hash, physical_page, extractor
            )
            all_pages_reused = all_pages_reused and page_reused
            completed_pages.append(page)
            self.progress(
                {
                    "event": "page_processed",
                    "asset_id": asset.asset_id,
                    "file_name": asset.display_file_name,
                    "physical_page": physical_page,
                    "page_count": asset.page_count,
                    "decision_status": page["decision_status"],
                }
            )
            self._write_json(
                cache_path,
                {
                    "status": "processing",
                    "sha256": asset.sha256,
                    "config_hash": config_hash,
                    "expected_page_count": asset.page_count,
                    "pages": completed_pages,
                },
            )
        document = {
            "schema_version": "1",
            "scope": "full_unique_content",
            "asset_id": asset.asset_id,
            "document_version_id": asset.document_version_id,
            "file_name": asset.display_file_name,
            "sha256": asset.sha256,
            "source_uri": asset.metadata.get("official_source_uri", ""),
            "processing_run_id": f"run_{config_hash[:12]}",
            "config_hash": config_hash,
            "metadata": asset.metadata,
            "source_files": list(asset.source_files),
            "canonical_rel_path": asset.canonical_rel_path,
            "pages": completed_pages,
        }
        self._write_json(
            cache_path,
            {
                "status": "complete",
                "sha256": asset.sha256,
                "config_hash": config_hash,
                "expected_page_count": asset.page_count,
                "pages": completed_pages,
                "document": document,
            },
        )
        return document, was_reused or all_pages_reused

    def build(self) -> dict[str, Any]:
        catalog = build_source_catalog(self.root)
        config_hash = _stable_json_sha(
            {"schema_version": "1", "page_extractor": self.page_extractor.config_id}
        )
        corpus_fingerprint = _stable_json_sha(
            {
                "catalog": catalog.fingerprint_payload(),
                "config_hash": config_hash,
                "artifact_profile": _CORPUS_ARTIFACT_PROFILE,
            }
        )
        corpus_version = f"corpus-{corpus_fingerprint[:12]}"
        if self.worker_count == 1:
            document_results = [
                self._document(asset, config_hash, self.page_extractor)
                for asset in catalog.assets
            ]
        else:
            local_state = threading.local()

            def build_asset(asset: ContentAsset) -> tuple[dict[str, Any], bool]:
                extractor = getattr(local_state, "extractor", None)
                if extractor is None:
                    assert self.page_extractor_factory is not None
                    extractor = self.page_extractor_factory()
                    local_state.extractor = extractor
                return self._document(asset, config_hash, extractor)

            with ThreadPoolExecutor(max_workers=self.worker_count) as executor:
                document_results = list(executor.map(build_asset, catalog.assets))
        documents = [document for document, _ in document_results]
        reused = sum(int(was_reused) for _, was_reused in document_results)

        canonical_path = (
            self.root / "data" / "canonical" / f"{corpus_version}-documents.jsonl"
        )
        self._write_text(
            canonical_path,
            "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in documents),
        )
        evidence_prefix = f"{corpus_version}-evidence"
        evidence_result = build_evidence_units(
            self.root,
            canonical_path,
            self.root / "data" / "evidence",
            artifact_prefix=evidence_prefix,
            overwrite=True,
        )
        retrieval_prefix = f"{corpus_version}-chunks"
        retrieval_result = build_retrieval_chunks(
            self.root,
            self.root / "data" / "evidence" / f"{evidence_prefix}.jsonl",
            self.root / "data" / "retrieval",
            artifact_prefix=retrieval_prefix,
            overwrite=True,
        )
        statuses = Counter(
            page["decision_status"] for document in documents for page in document["pages"]
        )
        retrieval_path = (
            self.root / "data" / "retrieval" / f"{retrieval_prefix}.jsonl"
        )
        with retrieval_path.open(encoding="utf-8") as handle:
            chunk_ids = [
                json.loads(line)["chunk_id"] for line in handle if line.strip()
            ]
        manifest = {
            "schema_version": "1",
            "corpus_version": corpus_version,
            "status": "ready" if statuses.get("failed", 0) == 0 else "needs_attention",
            "generated_at": _utc_now(),
            "source_file_count": catalog.source_file_count,
            "unique_content_count": catalog.unique_content_count,
            "duplicate_copy_count": catalog.duplicate_copy_count,
            "page_count": sum(asset.page_count for asset in catalog.assets),
            "page_status_counts": dict(sorted(statuses.items())),
            "evidence_unit_count": evidence_result["summary"]["evidence_unit_count"],
            "chunk_count": retrieval_result["summary"]["chunk_count"],
            "document_version_ids": [
                document["document_version_id"] for document in documents
            ],
            "chunk_ids": chunk_ids,
            "canonical_documents": canonical_path.relative_to(self.root).as_posix(),
            "evidence_units": f"data/evidence/{evidence_prefix}.jsonl",
            "retrieval_chunks": f"data/retrieval/{retrieval_prefix}.jsonl",
            "canonical_documents_sha256": _sha256(canonical_path),
            "evidence_units_sha256": _sha256(
                self.root / "data" / "evidence" / f"{evidence_prefix}.jsonl"
            ),
            "retrieval_chunks_sha256": _sha256(
                retrieval_path
            ),
            "config_hash": config_hash,
            "artifact_profile": _CORPUS_ARTIFACT_PROFILE,
            "assets": [asdict(asset) for asset in catalog.assets],
        }
        manifest_path = self.root / "data" / "registry" / f"{corpus_version}.json"
        if manifest_path.is_file():
            stored_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            comparable = json.loads(json.dumps(manifest, ensure_ascii=False))
            comparable["generated_at"] = stored_manifest.get("generated_at")
            if stored_manifest != comparable:
                raise RuntimeError(
                    "immutable corpus manifest conflicts with rebuilt artifacts"
                )
        else:
            self._write_json(manifest_path, manifest)
            stored_manifest = manifest
        self._write_json(
            self.root / "data" / "registry" / "corpus-candidate.json",
            {
                "corpus_version": corpus_version,
                "status": stored_manifest["status"],
                "manifest": manifest_path.relative_to(self.root).as_posix(),
                "manifest_sha256": _sha256(manifest_path),
            },
        )
        return {
            **stored_manifest,
            "processed_content_count": catalog.unique_content_count - reused,
            "reused_content_count": reused,
        }


def page_extractor_config_hash(extractor: PageExtractor) -> str:
    return _stable_json_sha(
        {"schema_version": "1", "page_extractor": extractor.config_id}
    )


def pending_content_pages(
    project_root: Path, catalog: SourceCatalog, extractor: PageExtractor
) -> list[tuple[ContentAsset, int]]:
    service = CorpusUpdateService(project_root, page_extractor=extractor)
    config_hash = page_extractor_config_hash(extractor)
    pending: list[tuple[ContentAsset, int]] = []
    for asset in sorted(catalog.assets, key=lambda item: -item.page_count):
        completed: set[int] = set()
        asset_cache = service._cache_path(asset, config_hash)
        if asset_cache.is_file():
            payload = json.loads(asset_cache.read_text(encoding="utf-8"))
            if (
                payload.get("sha256") == asset.sha256
                and payload.get("config_hash") == config_hash
            ):
                completed.update(
                    int(page["physical_page"]) for page in payload.get("pages", [])
                )
        for physical_page in range(1, asset.page_count + 1):
            if physical_page in completed:
                continue
            if service._page_cache_path(
                asset, config_hash, physical_page
            ).is_file():
                continue
            pending.append((asset, physical_page))
    return pending


_PROCESS_EXTRACTORS: dict[tuple[bool, int, bool, bool], AutomaticPageExtractor] = {}


def warm_content_asset(
    project_root: Path,
    asset: ContentAsset,
    *,
    enable_ocr: bool,
    minimum_native_characters: int,
    table_recognition: bool,
    layout_recognition: bool,
) -> dict[str, Any]:
    """Process one content asset in a worker process and persist its page cache."""

    key = (
        enable_ocr,
        minimum_native_characters,
        table_recognition,
        layout_recognition,
    )
    extractor = _PROCESS_EXTRACTORS.get(key)
    if extractor is None:
        extractor = AutomaticPageExtractor(
            project_root=project_root,
            enable_ocr=enable_ocr,
            minimum_native_characters=minimum_native_characters,
            table_recognition=table_recognition,
            layout_recognition=layout_recognition,
        )
        _PROCESS_EXTRACTORS[key] = extractor
    config_hash = _stable_json_sha(
        {"schema_version": "1", "page_extractor": extractor.config_id}
    )
    document, reused = CorpusUpdateService(
        project_root, page_extractor=extractor
    )._document(asset, config_hash, extractor)
    return {
        "asset_id": asset.asset_id,
        "file_name": asset.display_file_name,
        "reused": reused,
        "page_count": len(document["pages"]),
        "page_status_counts": dict(
            sorted(Counter(page["decision_status"] for page in document["pages"]).items())
        ),
    }


def warm_content_page(
    project_root: Path,
    asset: ContentAsset,
    physical_page: int,
    *,
    enable_ocr: bool,
    minimum_native_characters: int,
    table_recognition: bool,
    layout_recognition: bool,
) -> dict[str, Any]:
    """Process one unique page in a worker process."""

    key = (
        enable_ocr,
        minimum_native_characters,
        table_recognition,
        layout_recognition,
    )
    extractor = _PROCESS_EXTRACTORS.get(key)
    if extractor is None:
        extractor = AutomaticPageExtractor(
            project_root=project_root,
            enable_ocr=enable_ocr,
            minimum_native_characters=minimum_native_characters,
            table_recognition=table_recognition,
            layout_recognition=layout_recognition,
        )
        _PROCESS_EXTRACTORS[key] = extractor
    config_hash = page_extractor_config_hash(extractor)
    page, reused = CorpusUpdateService(
        project_root, page_extractor=extractor
    )._page(asset, config_hash, physical_page, extractor)
    return {
        "asset_id": asset.asset_id,
        "physical_page": physical_page,
        "decision_status": page["decision_status"],
        "reused": reused,
    }
