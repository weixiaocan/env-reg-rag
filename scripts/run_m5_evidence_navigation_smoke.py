"""Verify stable evidence lookup and allowlisted PDF navigation over HTTP."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "eval_results" / "m5-evidence-navigation-v1.json"
BASE_URL = "http://127.0.0.1:8000"
EVIDENCE_ID = "ev_58337104c115f4ddb491ad736f859ae7"
DOCUMENT_ID = "doc_adf038d73f824faf"


def main() -> None:
    started = time.perf_counter()
    with httpx.Client(base_url=BASE_URL, timeout=30, trust_env=False) as client:
        evidence_response = client.get(f"/api/v1/evidence/{EVIDENCE_ID}")
        document_response = client.get(f"/api/v1/documents/{DOCUMENT_ID}")
        pdf_response = client.get(
            f"/api/v1/documents/{DOCUMENT_ID}/content",
            headers={"Range": "bytes=0-4"},
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    evidence_response.raise_for_status()
    document_response.raise_for_status()
    evidence = evidence_response.json()
    document = document_response.json()
    passed = (
        evidence["evidence_id"] == EVIDENCE_ID
        and evidence["usage_policy"] == "source_locator_only"
        and evidence["document_version_id"] == DOCUMENT_ID
        and evidence["physical_pages"] == [19]
        and evidence["heading_path"] == ["5.2.4"]
        and document["local_content_available"] is True
        and document["local_content_url"]
        == f"/api/v1/documents/{DOCUMENT_ID}/content"
        and pdf_response.status_code == 206
        and pdf_response.content == b"%PDF-"
    )
    report = {
        "evaluation_id": "m5-evidence-navigation-v1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "elapsed_ms": elapsed_ms,
        "passed": passed,
        "evidence": evidence,
        "document": document,
        "pdf_probe": {
            "status_code": pdf_response.status_code,
            "content_range": pdf_response.headers.get("content-range", ""),
            "prefix": pdf_response.content.decode("ascii", errors="replace"),
        },
    }
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": passed,
                "elapsed_ms": elapsed_ms,
                "evidence_id": evidence["evidence_id"],
                "page": evidence["physical_pages"],
                "clause": evidence["heading_path"],
                "pdf_status": pdf_response.status_code,
                "pdf_prefix": report["pdf_probe"]["prefix"],
                "report": str(OUTPUT_PATH),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
