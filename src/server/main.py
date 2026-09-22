"""Uvicorn import target for the local evidence workbench."""

from pathlib import Path
import os

from dotenv import load_dotenv

from src.server.fastapi_app import create_app
from src.server.runtime import build_query_services
from src.application.formula_preview import FormulaPreviewService
from src.application.formula_review import FormulaReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

services = build_query_services(project_root=PROJECT_ROOT)
app = create_app(
    query_service=services.answer,
    source_locator_service=services.source_lookup,
    document_catalog=services.documents,
    evidence_catalog=services.evidence,
    readiness_probe=services.readiness,
    answer_corpus_version=services.corpus_version,
    source_lookup_corpus_version=services.corpus_version,
    region_image_service=services.region_image,
    formula_preview=FormulaPreviewService(PROJECT_ROOT),
    formula_review=FormulaReviewService(
        PROJECT_ROOT, os.getenv("FORMULA_REVIEW_RUN", "5d3264515515e228")
    ),
)
