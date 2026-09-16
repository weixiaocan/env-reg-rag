"""Offline provenance migration and conservative PDF completeness diagnostics."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from src.domain.source_evidence import SourceObservation, require_relative_path, require_sha256


REGISTRY = "data/registry/source_evidence.jsonl"
REPORT = "data/registry/pdf_source_audit.json"
AUDIT_PROFILE = "offline-source-audit-v1"
LEGACY_FIELDS = {
    "official_source_uri": "official_source_uri", "source_authority": "source_authority",
    "source_review": "source_review", "publication_date": "publication_date",
    "effective_date": "effective_from", "effective_status": "effective_status",
    "local_file_match": "local_file_match", "selection_status": "selection_status",
    "review_note": "legacy_review_note", "std_no": "standard_number",
    "jurisdiction": "jurisdiction", "document_kind": "document_kind",
}


def stable_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv(root: Path, relative: str) -> list[dict[str, str]]:
    path = root / relative
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def registered_pdf_path(root: Path, relative: str) -> Path:
    require_relative_path(relative)
    path = (root / relative).resolve()
    raw = (root / "data/raw").resolve()
    if not raw.is_relative_to(root.resolve()) or not path.is_relative_to(raw) or path.suffix.lower() != ".pdf":
        raise ValueError("inventory PDF must stay within data/raw")
    return path


def _validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ValueError("source record must be an object")
    if record.get("schema_version") != "1":
        raise ValueError("unsupported source record schema")
    require_sha256(record.get("file_sha256", ""))
    if not isinstance(record.get("aliases"), list) or not isinstance(record.get("observations"), list):
        raise ValueError("source record requires aliases and observations")
    for alias in record["aliases"]:
        if not isinstance(alias, dict) or not isinstance(alias.get("file_name"), str):
            raise ValueError("malformed source alias")
        require_relative_path(alias.get("rel_path", ""))
        if not alias["rel_path"].startswith("data/raw/") or not alias["rel_path"].lower().endswith(".pdf"):
            raise ValueError("source aliases must identify raw PDFs")
    for item in record["observations"]:
        observation = SourceObservation.from_dict(item)
        if (observation.origin_kind == "document_extracted"
                and observation.evidence_ref["sha256"] != record["file_sha256"]):
            raise ValueError("document extraction refers to a different file")


def load_source_records(root: Path) -> dict[str, dict[str, Any]]:
    path = root / REGISTRY
    if not path.is_file():
        return {}
    records = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            _validate_record(record)
            key = record["file_sha256"]
            if key in records:
                raise ValueError("duplicate file hash in source registry")
            records[key] = record
    return records


def resolve_source_fields(observations: list[SourceObservation]) -> dict[str, Any]:
    grouped = defaultdict(list)
    for item in observations:
        item.validate()
        grouped[item.field].append(item)
    result = {}
    for field, items in sorted(grouped.items()):
        verified = {item.value for item in items if item.status == "verified"}
        raw_values = {item.value for item in items}
        # A missing classification is not a contradictory factual assertion.
        values = raw_values - {"unknown"} or raw_values
        preferred = verified or values
        status = "conflicting" if len(preferred) > 1 else "verified" if verified else "unverified"
        result[field] = {
            "value": next(iter(preferred)) if len(preferred) == 1 else None,
            "status": status, "has_disagreement": len(values) > 1,
            "observations": [item.to_dict() for item in items],
        }
    return result


def source_metadata(record: dict[str, Any]) -> dict[str, str]:
    """Summarize provenance without claiming a legacy assertion was reverified."""
    observations = [SourceObservation.from_dict(item) for item in record["observations"]]
    fields = resolve_source_fields(observations)
    statuses = {field: value["status"] for field, value in fields.items()}
    status = ("conflicting" if "conflicting" in statuses.values()
              else "has_verified_fields" if "verified" in statuses.values()
              else "legacy_unverified" if any(o.origin_kind == "legacy_record" for o in observations)
              else "unverified")
    metadata = {
        "source_evidence_status": status,
        "source_field_statuses": json.dumps(statuses, sort_keys=True, separators=(",", ":")),
        "source_evidence_sha256": stable_digest(record["observations"]),
        "source_review": "needs_review",
        "effective_status": "unknown",
    }
    for field in ("official_source_uri", "source_authority", "publication_date", "effective_from"):
        metadata[field] = ""
        if field in fields:
            metadata[field] = fields[field]["value"] or ""
    for field in ("standard_number", "document_kind", "jurisdiction"):
        item = fields.get(field)
        if item and item["status"] == "verified":
            metadata[field] = item["value"]
        elif item and item["status"] == "conflicting":
            metadata[field] = "unknown" if field != "standard_number" else ""
    for field, unknown in (("source_review", "needs_review"), ("effective_status", "unknown")):
        item = fields.get(field)
        if item:
            metadata[f"legacy_{field}"] = item["value"] or ""
            metadata[field] = item["value"] if item["status"] == "verified" else unknown
    return metadata


def _legacy_observations(row: dict[str, str], relative: str, index: int) -> list[dict[str, Any]]:
    ref = {"kind": "registry", "path": relative, "row": index + 2,
           "record_sha256": stable_digest(row)}
    return [SourceObservation(field=field, value=row[column].strip(), origin_kind="legacy_record",
                              status="unverified", evidence_ref=ref).to_dict()
            for column, field in LEGACY_FIELDS.items() if row.get(column, "").strip()]


def _document_observations(text: str, digest: str, page: int) -> list[dict[str, Any]]:
    """Extract a few explicit native cover claims; never infer an external check."""
    claims = []
    for match in re.finditer(r"\b(GB\s*/?\s*T?|CJJ|HJ|SL|JGJ|QX\s*/?\s*T?)\s*(\d+)\s*[-—–]\s*(\d{4})\b", text):
        prefix = re.sub(r"\s", "", match[1])
        if prefix in {"GBT", "QXT"}:
            prefix = prefix[:-1] + "/T"
        claims.append(("standard_number", f"{prefix} {match[2]}-{match[3]}", match[0]))
    for match in re.finditer(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?\s*(发布|实施)", text):
        value = f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            continue
        claims.append(("publication_date" if match[4] == "发布" else "effective_from", value, match[0]))
    return [SourceObservation(
        field=field, value=value, origin_kind="document_extracted", status="unverified",
        evidence_ref={"kind": "file", "sha256": digest, "physical_pages": [page], "excerpt": excerpt},
    ).to_dict() for field, value, excerpt in claims]


def _inspect_pdf(path: Path, expected_inventory_pages: int, fields: dict[str, Any], digest: str) -> dict[str, Any]:
    result = {"status": "unverified", "issues": [], "physical_page_count": None,
              "readable_page_count": 0, "scan_method": "native_page_readability_and_layout_hints",
              "scan_coverage": "none", "toc_found": False, "appendix_headings": [],
              "version_hints": [], "issue_pages": {}, "supported_by": [],
              "native_text_page_count": 0, "extracted_observations": []}
    try:
        doc = pymupdf.open(path)
    except Exception:
        result["issues"].append("unreadable_pdf")
        result["status"] = "suspected"
        return result
    with doc:
        result["physical_page_count"] = len(doc)
        if doc.needs_pass:
            result["issues"].append("encrypted_pdf")
        else:
            result["scan_coverage"] = "all_pages_native_only"
            previous_footer = None
            for index in range(len(doc)):
                try:
                    page = doc[index]
                    blocks = page.get_text("blocks")
                    text = "\n".join(str(block[4]) for block in blocks if block[6] == 0)
                    result["readable_page_count"] += 1
                    result["native_text_page_count"] += int(bool(text.strip()))
                    if index < 3:
                        result["extracted_observations"].extend(_document_observations(text, digest, index + 1))
                    if re.search(r"(?m)^\s*(目录|目\s*录|Contents)\s*$", text):
                        result["toc_found"] = True
                    for heading in re.findall(r"(?m)^\s*(附录\s*[A-ZＡ-Ｚ]|Appendix\s+[A-Z])", text):
                        result["appendix_headings"].append({"heading": heading, "physical_page": index + 1})
                    if index < 3:
                        for marker in ("节选", "摘录"):
                            if marker in text:
                                result["issues"].append("excerpt_marker")
                                result["issue_pages"].setdefault("excerpt_marker", []).append(index + 1)
                        if "征求意见稿" in text:
                            result["version_hints"].append({"hint": "draft_marker", "physical_page": index + 1})
                    footer = None
                    for block in blocks:
                        if block[6] == 0 and block[1] >= page.rect.height * 0.88:
                            match = re.fullmatch(r"\s*[-—]?\s*(\d{1,4})\s*[-—]?\s*", str(block[4]))
                            if match:
                                footer = int(match[1])
                    if (footer is not None and previous_footer is not None
                            and footer > 1 and footer != previous_footer + 1):
                        result["issues"].append("printed_page_sequence_discontinuity")
                        result["issue_pages"].setdefault("printed_page_sequence_discontinuity", []).append(index + 1)
                    previous_footer = footer
                except Exception:
                    result["issues"].append("unreadable_page")
                    result["issue_pages"].setdefault("unreadable_page", []).append(index + 1)
        if len(doc) != expected_inventory_pages:
            result["issues"].append("inventory_page_count_mismatch")
        expected = fields.get("expected_page_count")
        if expected and expected["status"] == "verified" and len(doc) != int(expected["value"]):
            result["issues"].append("expected_page_count_mismatch")
        elif expected and expected["status"] == "conflicting":
            result["issues"].append("conflicting_expected_page_count")
    result["issues"] = sorted(set(result["issues"]))
    complete = fields.get("completeness")
    if complete and complete["status"] == "conflicting":
        result["issues"].append("conflicting_completeness_evidence")
    elif complete and complete["value"] == "incomplete":
        result["issues"].append("declared_incomplete_document")
    if result["issues"]:
        result["status"] = "suspected"
    elif complete and complete["status"] == "verified" and complete["value"] == "complete":
        result["status"] = "evidence_supported"
        result["supported_by"] = [o for o in complete["observations"] if o["status"] == "verified"]
    return result


def audit_sources(root: Path) -> dict[str, Any]:
    """Read only: prepare records and diagnostics without networking or publishing."""
    root = root.resolve()
    inventory = _csv(root, "data/registry/inventory.csv")
    if not inventory:
        raise ValueError("inventory contains no PDF records")
    reviews = _csv(root, "data/registry/document_reviews.csv")
    records = load_source_records(root)
    grouped = defaultdict(list)
    names = defaultdict(set)
    seen_paths = set()
    for index, row in enumerate(inventory):
        relative = row["rel_path"].replace("\\", "/")
        path = registered_pdf_path(root, relative)
        digest = row["sha256"].lower()
        require_sha256(digest)
        if relative in seen_paths:
            raise ValueError("duplicate inventory path")
        seen_paths.add(relative)
        if file_digest(path) != digest:
            raise ValueError("inventory file hash mismatch")
        grouped[digest].append((index, row, path, relative))
        names[row["file_name"]].add(digest)
    reviews_by_name = defaultdict(list)
    for index, row in enumerate(reviews):
        if len(names.get(row["file_name"], set())) > 1:
            raise ValueError("legacy filename matches multiple different contents; resolve it explicitly")
        reviews_by_name[row["file_name"]].append((index, row))
    files = []
    for digest, entries in sorted(grouped.items()):
        record = records.setdefault(digest, {"schema_version": "1", "file_sha256": digest,
                                            "aliases": [], "observations": []})
        aliases = {alias["rel_path"]: alias for alias in record["aliases"]}
        observations = {stable_digest(item): item for item in record["observations"]}
        for index, row, _, relative in entries:
            aliases[relative] = {"file_name": row["file_name"], "rel_path": relative}
            added = _legacy_observations(row, "data/registry/inventory.csv", index)
            for review_index, review in reviews_by_name[row["file_name"]]:
                added.extend(_legacy_observations(review, "data/registry/document_reviews.csv", review_index))
            for item in added:
                observations[stable_digest(item)] = item
        record["aliases"] = [aliases[key] for key in sorted(aliases)]
        record["observations"] = [observations[key] for key in sorted(observations)]
        fields = resolve_source_fields([SourceObservation.from_dict(item) for item in record["observations"]])
        # Inspect one byte-identical copy, then verify each input stayed unchanged.
        check = _inspect_pdf(entries[0][2], int(entries[0][1]["pages"]), fields, digest)
        for item in check.pop("extracted_observations"):
            observations[stable_digest(item)] = item
        record["observations"] = [observations[key] for key in sorted(observations)]
        fields = resolve_source_fields([SourceObservation.from_dict(item) for item in record["observations"]])
        for _, row, path, relative in entries:
            if file_digest(path) != digest:
                raise ValueError("PDF changed during audit")
            file_check = json.loads(json.dumps(check))
            if file_check["physical_page_count"] != int(row["pages"]):
                file_check["status"] = "suspected"
                file_check["issues"] = sorted(set(file_check["issues"] + ["inventory_page_count_mismatch"]))
            files.append({"file_name": row["file_name"], "rel_path": relative,
                          "file_sha256": digest, "fields": fields, "completeness": file_check})
    return {
        "schema_version": "1", "audit_profile": AUDIT_PROFILE,
        "inventory_content_sha256": stable_digest(inventory),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": [records[key] for key in sorted(records)], "files": files,
        "summary": {"file_count": len(files), "unique_content_count": len(grouped),
                    "orphan_record_count": len(set(records) - set(grouped)),
                    "unmatched_legacy_review_count": sum(row["file_name"] not in names for row in reviews),
                    "completeness_status_counts": dict(Counter(f["completeness"]["status"] for f in files))},
    }


def write_audit(root: Path, result: dict[str, Any]) -> None:
    """Explicit, atomic-per-file persistence; never modifies PDFs or publishes indexes."""
    for record in result["records"]:
        _validate_record(record)
    hashes = [record["file_sha256"] for record in result["records"]]
    if len(hashes) != len(set(hashes)):
        raise ValueError("duplicate source record")
    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                      for record in result["records"])
    report = {key: value for key, value in result.items() if key != "records"}
    report["source_registry_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    for relative, content in ((REGISTRY, payload), (REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        temporary.replace(path)


def completeness_metadata(root: Path, *, inventory_rows: list[dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """Ignore stale/missing reports; only a matching registry may supply diagnostics."""
    path = root / REPORT
    if not path.is_file() or not (root / REGISTRY).is_file():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("source_registry_sha256") != file_digest(root / REGISTRY):
        return {}
    rows = inventory_rows if inventory_rows is not None else _csv(root, "data/registry/inventory.csv")
    if not rows or report.get("inventory_content_sha256") != stable_digest(rows):
        return {}
    if report.get("audit_profile") != AUDIT_PROFILE:
        return {}
    grouped = defaultdict(list)
    for item in report["files"]:
        grouped[item["file_sha256"]].append(item["completeness"])
    result = {}
    for digest, checks in grouped.items():
        issues = sorted({issue for check in checks for issue in check["issues"]})
        status = ("suspected" if issues else "evidence_supported"
                  if all(c["status"] == "evidence_supported" for c in checks) else "unverified")
        result[digest] = {"completeness_status": status,
                          "completeness_issues": json.dumps(issues, separators=(",", ":")),
                          "source_audit_status": "available",
                          "completeness_evidence_sha256": stable_digest(checks)}
    return result
