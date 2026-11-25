"""ASGI middleware."""

import contextlib

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.error_handlers import error_response
from app.errors import FileTooLargeError

# Room for the multipart boundaries and part headers that wrap the file in the request body.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


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
