"""FastAPI adapter for the evidence-constrained query application module."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src.application.query_service import QueryApplicationService
from src.adapters.inventory_document_catalog import InventoryDocumentCatalog
from src.application.evidence_source import EvidenceSourceService
from src.domain.query import QueryRequest
from src.server.http_errors import install_error_handlers
from src.server.query_dto import AnswerResultDto, EvidenceDto
from src.server.readiness import ReadinessProbe


class QueryHttpRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    question: str = Field(min_length=1)
    conversation_context: dict[str, str] = Field(default_factory=dict)
    mode: Literal["answer", "source_lookup"] = "answer"


class DocumentDto(BaseModel):
    document_version_id: str
    file_name: str
    standard_no: str
    document_kind: str
    jurisdiction: str
    source_uri: str
    local_content_available: bool
    local_content_url: str


def create_app(
    *,
    query_service: QueryApplicationService,
    source_locator_service: QueryApplicationService | None = None,
    document_catalog: InventoryDocumentCatalog | None = None,
    evidence_catalog: EvidenceSourceService | None = None,
    readiness_probe: ReadinessProbe | None = None,
    answer_corpus_version: str = "formal-corpus-v1",
) -> FastAPI:
    """Create the HTTP adapter with its application dependency injected."""
    app = FastAPI(title="排水法规标准智能问答系统", version="0.1.0")
    install_error_handlers(app)
    workbench_path = Path(__file__).parent / "static" / "index.html"

    @app.get("/", response_class=FileResponse)
    async def workbench() -> FileResponse:
        return FileResponse(workbench_path)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/ready")
    async def ready() -> JSONResponse:
        if readiness_probe is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "checks": {"dependencies": "not_configured"},
                },
            )
        report = readiness_probe.check()
        return JSONResponse(
            status_code=200 if report.ready else 503,
            content={
                "status": "ready" if report.ready else "not_ready",
                "checks": report.checks,
            },
        )

    def registered_document(document_version_id: str):
        if document_catalog is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "document_catalog_unavailable",
                    "message": "document catalog is not configured",
                },
            )
        document = document_catalog.get(document_version_id)
        if document is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "document_not_found",
                    "message": "document is not registered",
                },
            )
        return document

    @app.get("/api/v1/documents/{document_version_id}", response_model=DocumentDto)
    async def get_document(document_version_id: str) -> DocumentDto:
        document = registered_document(document_version_id)
        return DocumentDto(
            document_version_id=document.document_version_id,
            file_name=document.file_name,
            standard_no=document.standard_no,
            document_kind=document.document_kind,
            jurisdiction=document.jurisdiction,
            source_uri=document.source_uri,
            local_content_available=document.local_path is not None,
            local_content_url=(
                f"/api/v1/documents/{document.document_version_id}/content"
            ),
        )

    @app.get("/api/v1/documents/{document_version_id}/content")
    async def get_document_content(document_version_id: str) -> FileResponse:
        document = registered_document(document_version_id)
        if document.local_path is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "document_content_not_found",
                    "message": "local PDF content is not available",
                },
            )
        return FileResponse(document.local_path, media_type="application/pdf")

    @app.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDto)
    async def get_evidence(evidence_id: str) -> EvidenceDto:
        if evidence_catalog is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "evidence_catalog_unavailable",
                    "message": "evidence catalog is not configured",
                },
            )
        evidence = evidence_catalog.get(evidence_id)
        if evidence is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "evidence_not_found",
                    "message": "evidence is not published",
                },
            )
        return EvidenceDto.model_validate(asdict(evidence))

    @app.post("/api/v1/queries", response_model=AnswerResultDto)
    async def submit_query(payload: QueryHttpRequest) -> AnswerResultDto:
        selected_service = query_service
        corpus_version = answer_corpus_version
        if payload.mode == "source_lookup":
            if source_locator_service is None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "source_lookup_unavailable",
                        "message": "source lookup is not configured",
                    },
                )
            selected_service = source_locator_service
            corpus_version = "m3-experiment-v1"
        result = await selected_service.execute(
            QueryRequest(
                request_id=payload.request_id,
                question=payload.question,
                conversation_context=payload.conversation_context,
                corpus_version=corpus_version,
                caller_type="web",
            )
        )
        return AnswerResultDto.model_validate(asdict(result))

    return app
