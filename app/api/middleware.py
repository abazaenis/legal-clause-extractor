"""ASGI middleware."""

import contextlib
import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_handlers import error_response
from app.errors import FileTooLargeError

log = structlog.get_logger(__name__)

# Room for the multipart boundaries and part headers that wrap the file in the request body.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

REQUEST_ID_HEADER = "X-Request-ID"
# What an id sent by the caller may look like. Anything else is replaced by one of
# our own, so a caller cannot write arbitrary text into the logs.
_ACCEPTABLE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestLogMiddleware:
    """Gives every request an id, and writes one log line when the request is done.

    The id is bound to the logging context, so every line that is logged while the
    request is handled carries it, whichever part of the service wrote the line. The
    id is also returned in the `X-Request-ID` header, so that a caller who reports a
    problem can say which request it was. A caller can send an id of its own in the
    same header, to follow one request through several services.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from(scope) or uuid.uuid4().hex
        # The context is cleared when a request starts and not when it ends. An error
        # nobody handled is logged after this middleware has let go of the request,
        # and that line needs the id as well.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        # For the handler of unhandled errors, whose response does not pass through here.
        scope.setdefault("state", {})["request_id"] = request_id

        # What the caller gets when the application fails before it answers.
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                # A plain number. Some responses carry it as an `HTTPStatus`, which
                # would be logged by its name.
                status_code = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            # The path only. A query string is the caller's data.
            log.info(
                "request_finished",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )


def _request_id_from(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode()
    for name, value in scope["headers"]:
        if name == wanted:
            candidate = value.decode("latin-1")
            return candidate if _ACCEPTABLE_REQUEST_ID.fullmatch(candidate) else None
    return None


class _BodyLimitExceededError(Exception):
    """Raised inside `receive` to stop the application from reading any further."""


class UploadSizeLimitMiddleware:
    """Refuse an oversized request body while it is still arriving.

    FastAPI reads the whole upload and writes it to a temporary file before the
    endpoint runs, and it has no limit on the total size. A check inside the endpoint
    would therefore only run after the server had already accepted everything.

    This middleware stops earlier, in two ways:

    - a declared Content-Length over the limit is refused before any body is read;
    - the body is counted as it streams in, because Content-Length can be missing
      (chunked uploads) or simply wrong.

    The limit here is on the whole request body, so it allows a little room for the
    multipart wrapping. The exact limit on the file itself is checked again once the
    file has been read.
    """

    def __init__(self, app: ASGIApp, max_upload_bytes: int) -> None:
        self.app = app
        self.max_upload_bytes = max_upload_bytes
        self.max_body_bytes = max_upload_bytes + MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared_length = _declared_content_length(scope)
        if declared_length is not None and declared_length > self.max_body_bytes:
            await self._reject(scope, receive, send)
            return

        received_bytes = 0
        limit_exceeded = False
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received_bytes, limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    limit_exceeded = True
                    raise _BodyLimitExceededError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if limit_exceeded:
                # Whatever the application answers after the limit was hit is dropped,
                # and replaced by the 413 below.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with contextlib.suppress(_BodyLimitExceededError):
            await self.app(scope, counting_receive, guarded_send)

        if limit_exceeded and not response_started:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = FileTooLargeError(self.max_upload_bytes)
        log.warning("request_refused", code=error.code, status_code=error.status_code)
        response = error_response(error.status_code, error.code, error.message)
        await response(scope, receive, send)


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
