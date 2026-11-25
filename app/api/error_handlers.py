"""Turns every failure into the same JSON shape."""

from collections.abc import Mapping
from http import HTTPStatus

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.schemas import ErrorDetail, ErrorResponse
from app.errors import AppError

log = structlog.get_logger(__name__)


def error_response(
    status_code: int, code: str, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """Build an error response. Every error the API returns goes through here."""
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


async def _handle_app_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, AppError)
    # The code says what happened. The message is for the caller and is not logged.
    # A status of 500 or more is the service's own trouble, or the LLM provider's.
    report = log.error if error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR else log.warning
    report("request_refused", code=error.code, status_code=error.status_code)
    return error_response(error.status_code, error.code, error.message)


async def _handle_invalid_request(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    # Say where each problem is and what it is, but never echo the submitted values.
    problems = "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    )
    status = HTTPStatus.UNPROCESSABLE_CONTENT
    log.warning(
        "request_refused", code="invalid_request", status_code=status.value, problems=problems
    )
    return error_response(status, "invalid_request", problems)


async def _handle_http_error(_request: Request, error: Exception) -> JSONResponse:
    """Errors raised by the framework itself, such as an unknown path or a wrong method."""
    assert isinstance(error, HTTPException)
    try:
        code = HTTPStatus(error.status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        code = "http_error"
    report = log.error if error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR else log.warning
    report("request_refused", code=code, status_code=error.status_code)
    return error_response(error.status_code, code, str(error.detail), error.headers)


async def _handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # The details go to the log. The caller only learns that something failed, and
    # gets the request id to quote. This response is sent from outside the request
    # middleware, so the id is added here.
    log.error("unhandled_error", exc_info=error)
    request_id = getattr(request.state, "request_id", None)
    return error_response(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected error occurred.",
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_invalid_request)
    app.add_exception_handler(HTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
