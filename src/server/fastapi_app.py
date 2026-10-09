"""FastAPI adapter for the evidence-constrained query application module."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from src.application.query_service import QueryApplicationService
from src.adapters.inventory_document_catalog import DocumentCatalog
from src.adapters.pdf_region_renderer import DocumentPageImageService, RegionImageService
from src.application.evidence_source import EvidenceSourceService
from src.application.formula_preview import FormulaPreviewService
from src.application.formula_review import FormulaReviewService
from src.domain.query import QueryRequest
from src.server.http_errors import install_error_handlers
from src.server.query_dto import AnswerResultDto, EvidenceDto
from src.server.readiness import ReadinessProbe
from src.server.rate_limit import QueryRateLimiter
from src.server.deployment_profile import DeploymentProfile


class QueryHttpRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    question: str = Field(min_length=1, max_length=1000)
    conversation_context: dict[str, str] = Field(default_factory=dict)
    mode: Literal["answer", "source_lookup"] = "answer"


class DocumentDto(BaseModel):
    document_version_id: str
    file_name: str
    standard_no: str
    document_kind: str
    jurisdiction: str
    source_uri: str
    aliases: list[str]
    local_page_images_available: bool
    page_count: int | None
    page_image_url_template: str


def _register_diagnostic_routes(
    app: FastAPI,
    *,
    formula_preview: FormulaPreviewService | None,
    formula_review: FormulaReviewService | None,
) -> None:
    """Register internal review routes only when explicitly enabled."""

    def preview_service():
        if formula_preview is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "formula_preview_unavailable",
                    "message": "formula preview is not configured",
                },
            )
        return formula_preview

    @app.get("/api/v1/formulas")
    def list_formulas():
        return {"items": preview_service().list(), "diagnostic_only": True}

    @app.get("/api/v1/formulas/{anchor_id}")
    def get_formula(anchor_id: str):
        item = preview_service().get(anchor_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "formula_not_found",
                    "message": "formula is not registered",
                },
            )
        return item

    def review_service():
        if formula_review is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "formula_review_unavailable"},
            )
        return formula_review

    @app.get("/api/v1/formula-review")
    def review_list(
        category: str = "all",
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ):
        service = review_service()
        if (
            category
            not in {
                "all",
                "relation_candidate",
                "expression_candidate",
                "fragment_candidate",
                "needs_review",
            }
            or offset < 0
            or not 1 <= limit <= 50
            or len(query) > 200
        ):
            raise HTTPException(
                status_code=400, detail={"code": "invalid_review_filter"}
            )
        try:
            return service.list(category, offset, limit, query=query)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(
                status_code=503,
                detail={"code": "formula_review_unavailable"},
            )

    @app.get("/api/v1/formula-review/{candidate_id}")
    def review_detail(candidate_id: str):
        try:
            item = review_service().get(candidate_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(
                status_code=503,
                detail={"code": "formula_review_unavailable"},
            )
        if item is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "formula_review_not_found"},
            )
        return item

    @app.get("/api/v1/formula-review/{candidate_id}/image")
    def review_image(
        candidate_id: str,
        context: bool = False,
        full_page: bool = False,
        page_offset: int = 0,
    ):
        if page_offset not in (-1, 0, 1) or (page_offset and not full_page):
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_source_page_offset"},
            )
        try:
            image = review_service().image(
                candidate_id, context, full_page, page_offset
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            raise HTTPException(
                status_code=503,
                detail={"code": "formula_image_unavailable"},
            )
        if image is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "formula_image_unavailable"},
            )
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )


def create_app(
    *,
    query_service: QueryApplicationService,
    source_locator_service: QueryApplicationService | None = None,
    document_catalog: DocumentCatalog | None = None,
    document_page_service: DocumentPageImageService | None = None,
    evidence_catalog: EvidenceSourceService | None = None,
    readiness_probe: ReadinessProbe | None = None,
    answer_corpus_version: str = "formal-corpus-v1",
    source_lookup_corpus_version: str = "m3-experiment-v1",
    formula_preview: FormulaPreviewService | None = None,
    formula_review: FormulaReviewService | None = None,
    region_image_service: RegionImageService | None = None,
    query_rate_limiter: QueryRateLimiter | None = None,
    deployment_profile: DeploymentProfile = "self_hosted",
    diagnostic_routes_enabled: bool = False,
) -> FastAPI:
    """Create the HTTP adapter with its application dependency injected."""
    app = FastAPI(title="排水法规标准智能问答系统", version="0.1.0")
    install_error_handlers(app)
    workbench_path = Path(__file__).parent / "static" / "index.html"
    documents_path = Path(__file__).parent / "static" / "documents.html"

    @app.get("/", response_class=FileResponse)
    async def workbench() -> FileResponse:
        return FileResponse(workbench_path)

    @app.get("/documents", response_class=FileResponse)
    async def document_browser() -> FileResponse:
        return FileResponse(documents_path)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/runtime")
    async def runtime_profile() -> dict[str, str]:
        return {
            "deployment_profile": deployment_profile,
            "answer_access": (
                "shared_demo_budget"
                if deployment_profile == "demo"
                else "operator_configured"
            ),
        }

    if diagnostic_routes_enabled:
        _register_diagnostic_routes(
            app,
            formula_preview=formula_preview,
            formula_review=formula_review,
        )

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

    def document_dto(document) -> DocumentDto:
        return DocumentDto(
            document_version_id=document.document_version_id,
            file_name=document.file_name,
            standard_no=document.standard_no,
            document_kind=document.document_kind,
            jurisdiction=document.jurisdiction,
            source_uri=document.source_uri,
            aliases=list(document.aliases),
            local_page_images_available=(
                document.local_path is not None and document_page_service is not None
            ),
            page_count=(
                document_page_service.page_count(document.document_version_id)
                if document_page_service is not None
                else None
            ),
            page_image_url_template=(
                f"/api/v1/documents/{document.document_version_id}/pages/{{physical_page}}/image"
            ),
        )

    @app.get("/api/v1/documents", response_model=list[DocumentDto])
    async def list_documents() -> list[DocumentDto]:
        if document_catalog is None or not callable(getattr(document_catalog, "list", None)):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "document_catalog_unavailable",
                    "message": "browsable document catalog is not configured",
                },
            )
        return [document_dto(document) for document in document_catalog.list()]

    @app.get("/api/v1/documents/{document_version_id}", response_model=DocumentDto)
    async def get_document(document_version_id: str) -> DocumentDto:
        document = registered_document(document_version_id)
        return document_dto(document)

    @app.get("/api/v1/documents/{document_version_id}/pages/{physical_page}/image")
    async def get_document_page_image(
        document_version_id: str, physical_page: int
    ) -> Response:
        registered_document(document_version_id)
        if document_page_service is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "document_page_service_unavailable",
                    "message": "document page rendering is not configured",
                },
            )
        png = document_page_service.image(document_version_id, physical_page)
        if png is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "document_page_not_found",
                    "message": "document page image is not available",
                },
            )
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

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

    @app.get("/api/v1/regions/{region_id}/image")
    async def get_region_image(region_id: str) -> Response:
        if region_image_service is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "region_image_unavailable",
                    "message": "region image rendering is not configured",
                },
            )
        png = region_image_service.image(region_id)
        if png is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "region_image_not_found",
                    "message": "source region image is not available",
                },
            )
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/queries", response_model=AnswerResultDto)
    async def submit_query(payload: QueryHttpRequest, request: Request) -> AnswerResultDto:
        if query_rate_limiter is not None:
            client_key = request.client.host if request.client is not None else "unknown"
            decision = query_rate_limiter.check(client_key, payload.mode)
            if not decision.allowed:
                if decision.reason in {
                    "answer_client_daily_limit_exceeded",
                    "answer_daily_budget_exhausted",
                }:
                    message = (
                        "今日 AI 演示额度已用完；仍可使用只找原文和资料浏览"
                    )
                else:
                    message = "public query limit exceeded; retry later"
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "rate_limit_exceeded",
                        "message": message,
                    },
                    headers={"Retry-After": str(decision.retry_after_seconds)},
                )
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
            corpus_version = source_lookup_corpus_version
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
