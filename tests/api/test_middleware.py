"""The upload size limit is enforced while the body arrives, not after."""

from starlette.types import Message, Receive, Scope, Send

from app.api.middleware import MULTIPART_OVERHEAD_BYTES, UploadSizeLimitMiddleware

MAX_UPLOAD_BYTES = 10
MAX_BODY_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES


def http_scope(content_length: int | None = None) -> Scope:
    headers = [] if content_length is None else [(b"content-length", str(content_length).encode())]
    return {"type": "http", "method": "POST", "path": "/", "headers": headers}


def body_in_chunks(total_bytes: int, chunk_size: int = 16 * 1024) -> Receive:
    """A `receive` callable that delivers `total_bytes` of body in several messages."""
    remaining = total_bytes

    async def receive() -> Message:
        nonlocal remaining
        size = min(chunk_size, remaining)
        remaining -= size
        return {"type": "http.request", "body": b"0" * size, "more_body": remaining > 0}

    return receive


class SentMessages:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def statuses(self) -> list[int]:
        return [m["status"] for m in self.messages if m["type"] == "http.response.start"]


async def read_whole_body(receive: Receive) -> int:
    total = 0
    while True:
        message = await receive()
        total += len(message.get("body", b""))
        if not message.get("more_body"):
            return total


async def test_declared_length_over_the_limit_is_refused_without_reading_the_body() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("The application must not be called.")

    async def receive() -> Message:
        raise AssertionError("The body must not be read.")

    sent = SentMessages()
    middleware = UploadSizeLimitMiddleware(app, max_upload_bytes=MAX_UPLOAD_BYTES)

    await middleware(http_scope(content_length=MAX_BODY_BYTES + 1), receive, sent)

    assert sent.statuses == [413]


async def test_body_over_the_limit_is_stopped_while_streaming() -> None:
    bytes_read_by_app = 0

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal bytes_read_by_app
        while True:
            message = await receive()
            bytes_read_by_app += len(message.get("body", b""))

    sent = SentMessages()
    middleware = UploadSizeLimitMiddleware(app, max_upload_bytes=MAX_UPLOAD_BYTES)

    await middleware(http_scope(), body_in_chunks(MAX_BODY_BYTES * 10), sent)

    assert sent.statuses == [413]
    # The application never saw the chunk that crossed the limit, nor anything after it.
    assert bytes_read_by_app <= MAX_BODY_BYTES


async def test_the_applications_own_answer_is_replaced_once_the_limit_is_hit() -> None:
    """FastAPI turns an error raised while it parses the body into its own 400."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await read_whole_body(receive)
        except Exception:
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"Bad request"})

    sent = SentMessages()
    middleware = UploadSizeLimitMiddleware(app, max_upload_bytes=MAX_UPLOAD_BYTES)

    await middleware(http_scope(), body_in_chunks(MAX_BODY_BYTES * 10), sent)

    assert sent.statuses == [413]


async def test_body_within_the_limit_reaches_the_application_untouched() -> None:
    bytes_read_by_app = 0

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal bytes_read_by_app
        bytes_read_by_app = await read_whole_body(receive)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = SentMessages()
    middleware = UploadSizeLimitMiddleware(app, max_upload_bytes=MAX_UPLOAD_BYTES)

    await middleware(
        http_scope(content_length=MAX_BODY_BYTES), body_in_chunks(MAX_BODY_BYTES), sent
    )

    assert bytes_read_by_app == MAX_BODY_BYTES
    assert sent.statuses == [204]


async def test_other_connection_types_are_passed_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    middleware = UploadSizeLimitMiddleware(app, max_upload_bytes=MAX_UPLOAD_BYTES)

    await middleware({"type": "lifespan"}, body_in_chunks(0), SentMessages())

    assert seen == ["lifespan"]
