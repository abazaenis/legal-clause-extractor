"""Turns every failure into the same JSON shape."""

import logging
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.schemas import ErrorDetail, ErrorResponse
from app.errors import AppError

logger = logging.getLogger(__name__)


def error_response(
    status_code: int, code: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Build an error response. Every error the API returns goes through here."""
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


async def _handle_app_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, AppError)
    return error_response(error.status_code, error.code, error.message)


async def _handle_invalid_request(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    # Say where each problem is and what it is, but never echo the submitted values.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    )
    return error_response(HTTPStatus.UNPROCESSABLE_CONTENT, "invalid_request", problems)


async def _handle_http_error(_request: Request, error: Exception) -> JSONResponse:
    """Errors raised by the framework itself, such as an unknown path or a wrong method."""
    assert isinstance(error, HTTPException)
    try:
        code = HTTPStatus(error.status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        code = "http_error"
    return error_response(error.status_code, code, str(error.detail), error.headers)


async def _handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
    # The details go to the log. The caller only learns that something failed.
    logger.error("Unhandled error while serving a request", exc_info=error)
    return error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred."
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_invalid_request)
    app.add_exception_handler(HTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
