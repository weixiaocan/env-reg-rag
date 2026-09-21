"""Stable JSON error mapping for every public HTTP interface."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


LOGGER = logging.getLogger(__name__)


def _response(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install the complete public error contract in one place."""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _response(
            status_code=422,
            code="invalid_request",
            message="request validation failed",
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        if isinstance(detail, dict) and {"code", "message"} <= detail.keys():
            return _response(
                status_code=error.status_code,
                code=str(detail["code"]),
                message=str(detail["message"]),
            )
        return _response(
            status_code=error.status_code,
            code="http_error",
            message=str(detail),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        LOGGER.error(
            "Unhandled HTTP error for %s %s",
            request.method,
            request.url.path,
            exc_info=(type(error), error, error.__traceback__),
        )
        return _response(
            status_code=500,
            code="internal_error",
            message="an unexpected error occurred",
        )
