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

from src.ingestion.native_pdf import NativePdfParser
from src.ingestion.ocr_pdf import OcrPdfParser, PaddleTextOcrEngine
from src.application.publish_state import CandidateConflictError
from src.application.document_relations import apply_relations
from src.ingestion.ocr_region_text import OCR_VOTE_MODE
from src.application.evidence_policy import EVIDENCE_POLICY_VERSION
from src.application.formula_region_gate import load_formula_region_gate
from src.application.pdf_source_audit import (
    REGISTRY as SOURCE_EVIDENCE_REGISTRY,
    load_source_records,
    source_metadata,
    completeness_metadata,
    registered_pdf_path,
)


SOURCE_REVIEW_RANK = {
    "official_fulltext_verified": 4,
    "official_record_found": 3,
    "needs_primary_source": 2,
    "needs_review": 1,
    "": 0,
}
from src.application.retrieval_context import RETRIEVAL_CONTEXT_VERSION  # noqa: E402
from src.application.canonical_assembly import ASSEMBLY_RULES_VERSION  # noqa: E402
from src.application.source_corrections import REGISTRY_PATH as SOURCE_CORRECTIONS_PATH  # noqa: E402
from src.application.table_checks import REGISTRY_PATH as TABLE_CHECKS_PATH  # noqa: E402

_CORPUS_ARTIFACT_PROFILE = "corpus-artifacts-v6-unified-original-cross-page-relations"


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
    document_relations: dict[str, Any]
    unique_page_count: int

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "source_file_count": self.source_file_count,
            "unique_content_count": self.unique_content_count,
            "duplicate_copy_count": self.duplicate_copy_count,
            "document_relations": self.document_relations,
            "unique_page_count": self.unique_page_count,
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
    apply_document_relations: bool = True,
) -> SourceCatalog:
    """Collapse physical duplicates while retaining every source-file record."""

    root = Path(project_root).resolve()
    source_records = load_source_records(root)
    has_source_registry = (root / SOURCE_EVIDENCE_REGISTRY).is_file()
    inventory_rows = _read_csv(
        inventory_path or root / "data" / "registry" / "inventory.csv"
    )
    if not inventory_rows:
        raise ValueError("inventory contains no PDF records")
    completeness_records = completeness_metadata(root, inventory_rows=inventory_rows) if has_source_registry else {}
    reviews = _read_csv(
        reviews_path or root / "data" / "registry" / "document_reviews.csv"
    )
    review_by_name = {row["file_name"]: row for row in reviews}
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in inventory_rows:
        digest = row.get("sha256", "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid inventory SHA-256: {row.get('rel_path', '')}")
        path = registered_pdf_path(root, row["rel_path"].replace("\\", "/"))
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
        if has_source_registry:
            record = source_records.get(digest, {
                "schema_version": "1", "file_sha256": digest,
                "aliases": [], "observations": [],
            })
            metadata.update(source_metadata(record))
            metadata.update(completeness_records.get(digest, {
                "completeness_status": "unverified", "source_audit_status": "missing_or_stale",
            }))
        else:
            # Compatibility values remain historical assertions, not new checks.
            metadata["source_evidence_status"] = "legacy_unverified"
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
    selected, relations = apply_relations(root, tuple(assets)) if apply_document_relations else (tuple(assets), {})
    return SourceCatalog(
        source_file_count=len(inventory_rows),
        unique_content_count=len(assets),
        duplicate_copy_count=len(inventory_rows) - len(assets),
        assets=selected,
        document_relations=relations,
        unique_page_count=sum(asset.page_count for asset in assets),
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
            f"auto-page-v9:native-min={minimum_native_characters}:ocr={enable_ocr}:native-quality=mojibake-v2:"
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
        # T02: persist the OCR engine name/version and page-level mean confidence
        # so they flow losslessly into V2 Canonical provenance. Pre-T02
        # page-intermediate caches do not carry these fields; assembly reads them
        # optionally (``.get``) and falls back to safe defaults. Native pages
        # have no per-page confidence (lexical quality only) -> None.
        is_ocr = source["extraction_route"] == "full_ocr"
        raw = source.get("raw") or {}
        metrics = (source.get("quality") or {}).get("metrics") or {}
        return {
            "physical_page": source["physical_page"],
            "page_index": source["page_index"],
            "display_page_label": source.get("display_page_label"),
            "width": source["width"],
            "height": source["height"],
            "rotation": source["rotation"],
            "extraction_route": source["extraction_route"],
            "parser_name": (
                raw.get("engine_name", "paddleocr-text") if is_ocr else "pymupdf-native"
            ),
            "parser_profile": "full-corpus-auto-v1",
            "engine_version": raw.get("engine_version") if is_ocr else None,
            "ocr_confidence": metrics.get("mean_confidence") if is_ocr else None,
            "decision_status": status,
            "decision_reasons": reasons,
            "publishable": status == "approved",
            "text": source.get("text") or "",
            "elements": source.get("elements") or [],
            "tables": AutomaticPageExtractor._canonical_tables(source.get("tables") or []),
            "raw_artifact_ref": "",
            "coordinate_normalizations": source.get('coordinate_normalizations') or [],
        }

    def extract(self, asset: ContentAsset, *, physical_page: int) -> dict[str, Any]:
        path = self.project_root / asset.canonical_rel_path
        native = self.native.parse_page(path, physical_page=physical_page)
        count = native["quality"]["metrics"]["non_whitespace_characters"]
        if native["extraction_route"] == "scanned_with_text_layer":
            return self._page(
                native,
                status="quarantine",
                reasons=["scanned_with_text_layer_requires_ocr_vote"],
            )
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
        # P3: OCR may still return symbol garbage on broken renders — do not approve it.
        if status == "approved":
            from src.ingestion.text_quality import score_text
            post = score_text(ocr.get("text") or "")
            if post["status"] == "fail" and any(
                r in post["reasons"]
                for r in ("mojibake_symbol_text", "low_han_ratio", "encoded_glyph_markers")
            ):
                status = "quarantine"
                reasons = list(dict.fromkeys(reasons + post["reasons"] + ["ocr_text_unreadable"]))
        # T08: a native->OCR re-route must never SILENTLY lose most of the
        # candidate text. When the rejected native layer held substantially
        # more content than the OCR pass recovered, quarantine for review.
        if status == "approved" and count >= self.minimum_native_characters:
            ocr_count = len(re.sub(r"\s", "", ocr.get("text") or ""))
            if ocr_count * 2 < count:
                status = "quarantine"
                reasons = list(dict.fromkeys(
                    reasons + ["native_text_rejected_ocr_text_shrunk"]))
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
        formula_run_id: str | None = None,
        attempted_contents: set[str] | None = None,
        recovery_dir: Path | None = None,
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
        self.formula_run_id = formula_run_id
        self.attempted_contents = attempted_contents or set()
        # T06/T07: surgical recovery (e.g. re-extracted QXT pages) enters a
        # build ONLY as an explicit TEXT-STAGE override input from this
        # directory -- never as an in-place patch of published artifacts.
        # The recovered pages change the DOCUMENTS, and the final corpus
        # version hash covers the documents -- so an override build always
        # produces a NEW corpus version (the input fingerprint is unchanged).
        self.recovery_dir = Path(recovery_dir) if recovery_dir is not None else None

    def _recovery_dir_identity(self) -> str:
        """Canonical project-relative posix identity of the recovery dir.

        Everything derived from it (page provenance, corpus_version hash)
        must be machine-independent; an unrelativizable path is a hard error.
        """
        try:
            return self.recovery_dir.relative_to(self.root).as_posix()
        except ValueError:
            raise ValueError(
                f"recovery_dir {str(self.recovery_dir)!r} is not inside the "
                "project root -- refusing to record an absolute recovery "
                "path in page provenance (corpus version ids must be "
                "machine-independent)")

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

    def _recovery_page_path(self, sha256: str, physical_page: int) -> Path:
        # T07 Finding A: recovery pages are read from the CONFIGURED recovery
        # directory through its canonical project-relative identity -- a dir
        # outside the project root fails closed here instead of silently
        # reading (or ignoring) pages at a hardcoded location.
        return (
            self.root
            / self._recovery_dir_identity()
            / sha256
            / f"{physical_page:04d}.json"
        )

    def recovery_page(self, sha256: str, physical_page: int) -> dict[str, Any] | None:
        """Return the integrity-checked recovery override page, if present."""
        if self.recovery_dir is None:
            return None
        path = self._recovery_page_path(sha256, physical_page)
        if not path.is_file():
            return None
        page, integrity = self._read_checkpoint(path)
        if not integrity:
            raise ValueError(
                f"recovery override page failed its integrity check: {path}")
        return page

    def _has_recovery_pages(self, asset: ContentAsset) -> bool:
        """True when any recovery override page exists for this asset.

        A build given recovery overrides must re-derive the affected
        documents from the overrides instead of silently reusing a cached
        checkpoint that predates the recovery.
        """
        if self.recovery_dir is None:
            return False
        return any(
            self.recovery_page(asset.sha256, page) is not None
            for page in range(1, asset.page_count + 1)
        )

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _write_checkpoint(self, path: Path, payload: Any) -> None:
        self._write_json(path, {**payload, 'checkpoint_sha256': _stable_json_sha(payload)})

    def _read_checkpoint(self, path: Path):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            return {}, False
        if not isinstance(payload, dict):
            return {}, False
        digest = payload.pop('checkpoint_sha256', None)
        return payload, digest == _stable_json_sha(payload)

    def _write_text(self, path: Path, content: str) -> None:
        """Publish a generated text artifact only after it is fully written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _write_candidate_text(self, path: Path, content: str) -> None:
        """T07: candidate artifacts are CREATE-ONLY and immutable.

        Absent -> atomic write; byte-identical -> quiet reuse; ANY other
        existing content -> CandidateConflictError raised BEFORE any write,
        so a rebuild never truncates or overwrites an existing candidate.
        """
        payload = content.encode("utf-8")
        if path.is_file():
            # _write_text runs in text mode, so the platform newline (CRLF on
            # Windows) is a storage artifact, not content: compare modulo it.
            if path.read_bytes().replace(b"\r\n", b"\n") == payload:
                return
            raise CandidateConflictError(
                "immutable candidate conflict: "
                f"{path.relative_to(self.root).as_posix()} already exists "
                "with different content -- existing candidate left untouched")
        self._write_text(path, content)

    def _write_candidate_json(self, path: Path, payload: Any) -> None:
        """Create-only JSON candidate write (same contract, _write_json form).

        The only tolerated difference on reuse is ``generated_at`` -- a pure
        informational wall-clock stamp that has never taken part in the
        manifest immutability comparison. Everything else must be identical.
        """
        if path.is_file():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise CandidateConflictError(
                    "immutable candidate conflict: "
                    f"{path.relative_to(self.root).as_posix()} exists but is "
                    "unreadable -- existing candidate left untouched") from exc
            comparable = json.loads(json.dumps(payload, ensure_ascii=False))
            if isinstance(stored, dict) and isinstance(comparable, dict):
                stored = {k: v for k, v in stored.items() if k != "generated_at"}
                comparable = {k: v for k, v in comparable.items()
                              if k != "generated_at"}
            if stored == comparable:
                return
            raise CandidateConflictError(
                "immutable candidate conflict: "
                f"{path.relative_to(self.root).as_posix()} already exists "
                "with different content -- existing candidate left untouched")
        self._write_json(path, payload)

    def _validate_recovery_inputs(self, catalog) -> None:
        """T07: an explicit recovery dir is a FULLY VALIDATED build input.

        Runs BEFORE any candidate write: a missing/empty directory, a
        missing or inconsistent recovery record, unknown catalog sha, pages
        missing / duplicated / out of range, failed checkpoint integrity or
        a text-whitelist violation all HARD-FAIL the build -- a broken
        recovery input can never silently degrade into a recovery-less one.
        """
        from src.application.unified_pdf import UnifiedPageExtractor
        recovery_rel = self._recovery_dir_identity()
        if not self.recovery_dir.is_dir():
            raise ValueError(
                f"recovery dir does not exist: {recovery_rel} -- refusing to "
                "build without the explicitly requested recovery input")
        by_sha = {asset.sha256: asset for asset in catalog.assets}
        sha_dirs = [d for d in sorted(self.recovery_dir.iterdir())
                    if d.is_dir()]
        if not sha_dirs:
            raise ValueError(
                f"recovery dir is empty: {recovery_rel} -- refusing to build "
                "without the explicitly requested recovery input")
        for sha_dir in sha_dirs:
            sha = sha_dir.name
            asset = by_sha.get(sha)
            if asset is None:
                raise ValueError(
                    f"recovery document {sha[:12]}... is not in the catalog")
            record_path = sha_dir / "recovery_record.json"
            if not record_path.is_file():
                raise ValueError(
                    f"recovery record missing for {sha[:12]}... "
                    f"(expected {record_path.name})")
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise ValueError(
                    f"un readable recovery record for {sha[:12]}...") from exc
            record_dir = f"{recovery_rel}/{sha}"
            if (record.get("schema_version") != "t06-recovery-1"
                    or record.get("sha256") != sha
                    or record.get("recovery_dir") != record_dir):
                raise ValueError(
                    f"recovery record inconsistent for {sha[:12]}...: it "
                    "must bind the directory sha256 and the canonical "
                    f"recovery path {record_dir!r}")
            page_files = sorted(p for p in sha_dir.glob("*.json")
                                if p.name != "recovery_record.json")
            if not page_files:
                raise ValueError(f"no recovery pages for {sha[:12]}...")
            file_pages: list[int] = []
            for page_path in page_files:
                match = re.fullmatch(r"(\d{4})\.json", page_path.name)
                if not match:
                    raise ValueError(
                        f"unexpected recovery page file name: {page_path.name}")
                page_no = int(match.group(1))
                if not 1 <= page_no <= asset.page_count:
                    raise ValueError(
                        f"recovery page {page_no} out of range for "
                        f"{sha[:12]}... ({asset.page_count} pages)")
                file_pages.append(page_no)
            record_pages = record.get("pages")
            if not isinstance(record_pages, list) or sorted(
                    record_pages) != file_pages:
                raise ValueError(
                    f"recovery record pages {record_pages!r} do not match "
                    f"the page files {file_pages} for {sha[:12]}...")
            for page_path, page_no in zip(page_files, file_pages):
                page, integrity = self._read_checkpoint(page_path)
                if not integrity:
                    raise ValueError(
                        "recovery override page failed its integrity check: "
                        f"{page_path}")
                if page.get("physical_page") != page_no:
                    raise ValueError(
                        f"recovery page file {page_path.name} claims "
                        f"physical_page {page.get('physical_page')!r}")
                UnifiedPageExtractor._validate_recovered_text_page(
                    page, page_no)

    def _page(
        self,
        asset: ContentAsset,
        config_hash: str,
        physical_page: int,
        extractor: PageExtractor,
    ) -> tuple[dict[str, Any], bool]:
        # T07: a surgical recovery override (if the build was given one) is an
        # explicit, integrity-checked TEXT-STAGE input of THIS unified build.
        # It never bypasses the unified structure stages: the extractor still
        # runs layout/tables/formulas/figures on top of the recovered text.
        # Recovery-derived pages are NEVER written into the reusable caches --
        # a later build without --recovery-dir must not inherit the content.
        recovered = None
        if self.recovery_dir is not None:
            recovered = self.recovery_page(asset.sha256, physical_page)
        if recovered is not None:
            if not getattr(extractor, 'requires_structure', False):
                return recovered, True
            bound = {**recovered,
                     'recovery_checkpoint_sha256': _stable_json_sha(recovered)}
            page = extractor.extract(
                asset, physical_page=physical_page, recovery_text=bound)
            page["sample_id"] = f"P-{asset.sha256[:8]}-{physical_page:04d}"
            page["raw_artifact_ref"] = self._cache_path(
                asset, config_hash
            ).relative_to(self.root).as_posix()
            page["recovery_text_source"] = {
                # T07 Finding A: this provenance enters the document -> the
                # corpus_version hash, so it MUST be a project-relative posix
                # path (same content -> same version on every machine).
                # Absolute paths fail closed, never fall back silently.
                "recovery_dir": self._recovery_dir_identity(),
                "page_file": f"{physical_page:04d}.json",
                "checkpoint_sha256": bound["recovery_checkpoint_sha256"],
            }
            return page, False
        page_cache = self._page_cache_path(asset, config_hash, physical_page)
        if page_cache.is_file():
            page, integrity = self._read_checkpoint(page_cache)
            if integrity and page.get("physical_page") != physical_page:
                raise ValueError(f"page cache mismatch: {page_cache}")
            if (integrity or not getattr(extractor, 'requires_structure', False)) and page.get('decision_status') != 'failed' and (
                not getattr(extractor, 'requires_structure', False)
                or page.get('structure_execution_complete') is True
            ):
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
        self._write_checkpoint(page_cache, page)
        return page, False

    def _document(
        self, asset: ContentAsset, config_hash: str, extractor: PageExtractor
    ) -> tuple[dict[str, Any], bool]:
        cache_path = self._cache_path(asset, config_hash)
        # T06: recovery overrides invalidate document-level cache reuse --
        # the override pages must enter THIS build via _page.
        recovery_active = self._has_recovery_pages(asset)
        payload: dict[str, Any]
        if not recovery_active and cache_path.is_file():
            payload, integrity = self._read_checkpoint(cache_path)
            if (
                payload.get("sha256") == asset.sha256
                and payload.get("config_hash") == config_hash
                and payload.get("status") == "complete"
                and len(payload.get("pages", [])) == asset.page_count
                and (integrity or not getattr(extractor, 'requires_structure', False))
                and (asset.sha256 in self.attempted_contents or all(p.get('decision_status') != 'failed' and (
                    not getattr(extractor, 'requires_structure', False)
                    or p.get('structure_execution_complete') is True
                ) for p in payload.get('pages', [])))
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
            and not recovery_active
            and payload.get("sha256") == asset.sha256
            and payload.get("config_hash") == config_hash
            and payload.get("status") == "processing"
            and not getattr(extractor, 'requires_structure', False)
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
            # T07: while a recovery override is active for this document, the
            # document cache is not written at all -- recovery content must
            # stay out of every cache a later build could reuse.
            if not recovery_active:
                self._write_checkpoint(
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
        if not recovery_active:
            self._write_checkpoint(
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

    def _recovery_provenance(self, catalog) -> dict[str, Any]:
        """Corpus-level recovery provenance for the registry manifest (T07).

        Records the canonical recovery directory and every recovery page's
        checkpoint digest, so the manifest alone proves the recovery channel
        participated in this build. Page files are re-hashed here -- a
        recovery page that fails its checkpoint integrity is a hard error.
        """
        if self.recovery_dir is None:
            return {"schema_version": "t07-recovery-manifest-1",
                    "recovery_dir": None, "documents": []}
        try:
            recovery_rel = self.recovery_dir.relative_to(self.root).as_posix()
        except ValueError:
            raise ValueError(
                f"recovery_dir {str(self.recovery_dir)!r} is not inside the "
                "project root -- refusing to record an absolute recovery path "
                "in the manifest (corpus version ids must be machine-independent)")
        catalog_shas = {asset.sha256 for asset in catalog.assets}
        documents: list[dict[str, Any]] = []
        if not self.recovery_dir.is_dir():
            return {"schema_version": "t07-recovery-manifest-1",
                    "recovery_dir": recovery_rel, "documents": []}
        for sha_dir in sorted(self.recovery_dir.iterdir()):
            if not sha_dir.is_dir() or sha_dir.name not in catalog_shas:
                continue
            pages: list[dict[str, Any]] = []
            for page_path in sorted(sha_dir.glob("*.json")):
                if page_path.name == "recovery_record.json":
                    continue
                page, integrity = self._read_checkpoint(page_path)
                if not integrity:
                    raise ValueError(
                        "recovery override page failed its integrity check: "
                        f"{page_path}")
                pages.append({
                    "physical_page": page.get("physical_page"),
                    "page_file": page_path.name,
                    "checkpoint_sha256": _stable_json_sha(page),
                })
            if pages:
                documents.append({"sha256": sha_dir.name, "pages": pages})
        return {"schema_version": "t07-recovery-manifest-1",
                "recovery_dir": recovery_rel, "documents": documents}

    def build(self) -> dict[str, Any]:
        catalog = build_source_catalog(self.root)
        if self.recovery_dir is not None:
            # T07: validate the full recovery input chain BEFORE any
            # candidate write -- inconsistencies hard-fail with zero writes.
            self._validate_recovery_inputs(catalog)
        formula_gate = load_formula_region_gate(self.root, self.formula_run_id, catalog) if self.formula_run_id else None
        config_hash = _stable_json_sha(
            {"schema_version": "1", "page_extractor": self.page_extractor.config_id}
        )
        fingerprint = {
            "catalog": catalog.fingerprint_payload(),
            "config_hash": config_hash,
            "artifact_profile": _CORPUS_ARTIFACT_PROFILE,
            # What gets indexed for retrieval is part of the corpus: a
            # change mints a new version instead of rebinding the
            # immutable stage chain of an existing one.
            "retrieval_context": RETRIEVAL_CONTEXT_VERSION,
            "ocr_vote": OCR_VOTE_MODE,
            "evidence_policy": EVIDENCE_POLICY_VERSION,
            "assembly_rules": ASSEMBLY_RULES_VERSION,
            "middle_layer_config_sha256": _sha256(self.root / "config" / "middle_layer_v3.json")
                if (self.root / "config" / "middle_layer_v3.json").is_file() else None,
            "formula_region_gate": formula_gate['provenance'] if formula_gate else None,
            "formula_region_registry_sha256": _sha256(self.root / 'data/registry/formula-gold.json')
                if (self.root / 'data/registry/formula-gold.json').is_file() else None,
        }
        # Assembly applies the owner-reviewed corrections, so they are part
        # of the corpus too; without the registry the fingerprint is unchanged.
        corrections = self.root / SOURCE_CORRECTIONS_PATH
        if corrections.is_file():
            fingerprint["source_corrections_sha256"] = _sha256(corrections)
        table_checks = self.root / TABLE_CHECKS_PATH
        if table_checks.is_file():
            fingerprint["table_checks_sha256"] = _sha256(table_checks)
        corpus_fingerprint = _stable_json_sha(fingerprint)
        corpus_version = f"corpus-{corpus_fingerprint[:12]}"
        priority_shas = set()
        if getattr(self.page_extractor, 'requires_structure', False):
            for name in ('formula-gold.json', 'table-cell-gold.json'):
                path = self.root / 'data/registry' / name
                if path.is_file():
                    priority_shas.update(a['file_sha256'] for a in json.loads(path.read_text(encoding='utf-8'))['anchors'])
        processing_assets = sorted(catalog.assets, key=lambda a: (a.sha256 not in priority_shas, a.sha256))
        if self.worker_count == 1:
            document_results = [
                self._document(asset, config_hash, self.page_extractor)
                for asset in processing_assets
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
                document_results = list(executor.map(build_asset, processing_assets))
        document_results.sort(key=lambda result: result[0]['sha256'])
        documents = [document for document, _ in document_results]
        structure_audit = None
        if getattr(self.page_extractor, 'requires_structure', False):
            from src.application.unified_pdf import enrich_original_relations
            from src.evaluation.unified_pdf_audit import audit_documents
            enrich_original_relations(documents)
            structure_audit = audit_documents(self.root, catalog, documents)
        # Failed and recovered outputs must have different immutable versions.
        # Input-only fingerprints otherwise overwrite artifacts before rejecting
        # the old needs_attention manifest during a retry.
        corpus_version = 'corpus-' + _stable_json_sha({
            'input_fingerprint': corpus_fingerprint, 'documents': documents,
            'structure_audit': structure_audit,
        })[:12]
        reused = sum(int(was_reused) for _, was_reused in document_results)

        canonical_path = (
            self.root / "data" / "canonical" / f"{corpus_version}-documents.jsonl"
        )
        self._write_candidate_text(
            canonical_path,
            "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in documents),
        )
        statuses = Counter(
            page["decision_status"] for document in documents for page in document["pages"]
        )
        structure_required = getattr(self.page_extractor, 'requires_structure', False)
        structure_failures = [
            {'file_sha256': d['sha256'], 'file_name': d['file_name'],
             'physical_page': p['physical_page'], 'layout_status': p.get('layout_status'),
             'layout_failure': p.get('layout_failure'),
             'regions': [{'region_id': r['region_id'], 'bbox': r.get('bbox'),
                          'kind': r['kind'], 'execution_status': r['execution_status'],
                          'issues': r['issues']} for r in p.get('regions', [])
                         if r['execution_status'] != 'completed']}
            for d in documents for p in d['pages']
            if structure_required and p.get('structure_execution_complete') is not True]
        manifest = {
            "schema_version": "1",
            "corpus_version": corpus_version,
            "status": "ready" if statuses.get("failed", 0) == 0 and not structure_failures
                and (structure_audit is None or structure_audit['artifact_integrity_passed']) else "needs_attention",
            "structure_processing": {
                'required': structure_required,
                'complete_page_count': sum(p.get('structure_execution_complete') is True
                                           for d in documents for p in d['pages']),
                'execution_failures': structure_failures,
                'region_count': sum(len(p.get('regions', [])) for d in documents for p in d['pages']),
                'layout_omission_quality': 'not_independently_verified' if structure_required else 'not_evaluated',
            },
            "generated_at": _utc_now(),
            "source_file_count": catalog.source_file_count,
            "unique_content_count": catalog.unique_content_count,
            "duplicate_copy_count": catalog.duplicate_copy_count,
            "selected_content_count": len(catalog.assets),
            "same_version_copy_count": catalog.unique_content_count - len(catalog.assets),
            "source_unique_page_count": catalog.unique_page_count,
            "document_relations": catalog.document_relations,
            "page_count": sum(asset.page_count for asset in catalog.assets),
            "page_status_counts": dict(sorted(statuses.items())),
            "document_version_ids": [
                document["document_version_id"] for document in documents
            ],
            "canonical_documents": canonical_path.relative_to(self.root).as_posix(),
            "canonical_documents_sha256": _sha256(canonical_path),
            "config_hash": config_hash,
            "artifact_profile": _CORPUS_ARTIFACT_PROFILE,
            "formula_region_gate": formula_gate['provenance'] if formula_gate else None,
            "assets": [asdict(asset) for asset in catalog.assets],
        }
        manifest["recovery_provenance"] = self._recovery_provenance(catalog)
        if structure_audit is not None:
            audit_path = self.root / 'data/canonical' / f'{corpus_version}-structure-audit.json'
            self._write_candidate_json(audit_path, structure_audit)
            manifest['structure_audit'] = audit_path.relative_to(self.root).as_posix()
            manifest['structure_audit_sha256'] = _sha256(audit_path)
        manifest_path = self.root / "data" / "registry" / f"{corpus_version}.json"
        # T07: create-only candidate write -- reuse requires identity modulo
        # the informational generated_at stamp; any other difference raises
        # CandidateConflictError and leaves the existing manifest untouched.
        self._write_candidate_json(manifest_path, manifest)
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
            "processed_content_count": len(catalog.assets) - reused,
            "reused_content_count": reused,
        }


def page_extractor_config_hash(extractor: PageExtractor) -> str:
    return _stable_json_sha(
        {"schema_version": "1", "page_extractor": extractor.config_id}
    )


def write_recovery_pages(
    project_root: Path, sha256: str, pages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Write surgically recovered pages as a unified-build override input (T06).

    This is the ONLY sanctioned output of a specialist recovery run (e.g. a
    re-extracted garbled QXT document): per-page files under
    ``data/canonical/recovery/<sha256>/`` (checkpoint-integrity protected)
    plus a recovery record. It NEVER touches the published page-intermediate
    jsonl, any manifest or the current pointer -- the recovered pages enter
    the corpus only when a unified ``build_corpus.py`` run passes the
    recovery directory as ``recovery_dir``, which produces a NEW corpus
    version (the fingerprint covers the documents).
    """
    root = Path(project_root)
    recovery_dir = root / "data" / "canonical" / "recovery" / sha256
    recovery_dir.mkdir(parents=True, exist_ok=True)
    written: list[int] = []
    for page in pages:
        physical_page = int(page["physical_page"])
        payload = {**page, "checkpoint_sha256": _stable_json_sha(page)}
        path = recovery_dir / f"{physical_page:04d}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        tmp.replace(path)
        written.append(physical_page)
    record = {
        "schema_version": "t06-recovery-1",
        "sha256": sha256,
        "recovery_dir": recovery_dir.relative_to(root).as_posix(),
        "pages": sorted(written),
        "written_at": _utc_now(),
    }
    record_path = recovery_dir / "recovery_record.json"
    tmp = record_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(record_path)
    return record


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
