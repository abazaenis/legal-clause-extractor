"""POST /api/extract: the upload checks and the parsing, up to the step not built yet."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient, Response

from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.config import Settings
from app.main import create_app
from tests.helpers import build_pdf

URL = "/api/extract"
UploadField = dict[str, tuple[str, bytes, str]]


def upload(
    data: bytes, filename: str = "nda.pdf", media_type: str = "application/pdf"
) -> UploadField:
    return {"file": (filename, data, media_type)}


def padding_that_starts_like_a_pdf(size: int) -> bytes:
    """Exactly `size` bytes with a PDF header. Enough for the size checks, which run
    before anything tries to read the file."""
    header = b"%PDF-1.7\n"
    return header + b"0" * (size - len(header))


def error_code(response: Response) -> str:
    code = response.json()["error"]["code"]
    assert isinstance(code, str)
    return code


def assert_parsed(response: Response) -> None:
    """A valid upload is parsed and then stops at the pipeline step that is not built yet.

    When the rest of the pipeline exists, this is the one place to change.
    """
    assert response.status_code == 501
    assert error_code(response) == "not_implemented"
    assert "was parsed" in response.json()["error"]["message"]


@asynccontextmanager
async def client_with(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client for an application built with its own settings."""
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --- Valid uploads -----------------------------------------------------------------------


async def test_valid_pdf_is_parsed(client: AsyncClient) -> None:
    pdf = build_pdf(
        [["1. Definitions.", "Some text."], ["2. Term."]],
        header="ACME CONFIDENTIAL",
        footer="Page {page} of {pages}",
    )

    response = await client.post(URL, files=upload(pdf))

    assert_parsed(response)
    assert response.json()["error"]["message"] == (
        "nda.pdf was parsed (7 lines on 2 pages, 4 flagged as page noise), "
        "but clause extraction is not implemented yet."
    )


async def test_pdf_is_accepted_whatever_the_client_calls_it(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]])

    response = await client.post(URL, files=upload(pdf, "notes.txt", "text/plain"))

    assert_parsed(response)


async def test_file_exactly_at_the_size_limit_is_accepted() -> None:
    pdf = build_pdf([["1. Definitions."]])

    async with client_with(Settings(max_upload_bytes=len(pdf), _env_file=None)) as client:
        response = await client.post(URL, files=upload(pdf))

    assert_parsed(response)


# --- Checks that need no parser ------------------------------------------------------------


async def test_missing_file_field_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, data={"something_else": "value"})

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert "file" in response.json()["error"]["message"]


async def test_empty_file_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, files=upload(b""))

    assert response.status_code == 422
    assert error_code(response) == "empty_file"


async def test_content_decides_the_file_type_not_the_name_or_declared_type(
    client: AsyncClient,
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    response = await client.post(URL, files=upload(png, "contract.pdf", "application/pdf"))

    assert response.status_code == 415
    assert error_code(response) == "unsupported_file_type"


async def test_word_documents_are_not_supported_yet(client: AsyncClient) -> None:
    docx = b"PK\x03\x04" + b"0" * 100
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    response = await client.post(URL, files=upload(docx, "contract.docx", media_type))

    assert response.status_code == 415
    assert error_code(response) == "unsupported_file_type"


async def test_file_one_byte_over_the_size_limit_is_rejected() -> None:
    # Small enough to pass the middleware's allowance for multipart wrapping, so this
    # exercises the exact check made after the file has been read.
    pdf = build_pdf([["1. Definitions."]])

    async with client_with(Settings(max_upload_bytes=len(pdf) - 1, _env_file=None)) as client:
        response = await client.post(URL, files=upload(pdf))

    assert response.status_code == 413
    assert error_code(response) == "file_too_large"


async def test_far_oversized_file_is_rejected(client: AsyncClient, settings: Settings) -> None:
    data = padding_that_starts_like_a_pdf(
        settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES + 5000
    )

    response = await client.post(URL, files=upload(data))

    assert "content-length" in response.request.headers
    assert response.status_code == 413
    assert error_code(response) == "file_too_large"
    # The test limit is 64 KB.
    assert response.json()["error"]["message"] == "The file is larger than the limit of 64 KB."


async def test_oversized_upload_without_content_length_is_stopped_while_streaming(
    client: AsyncClient, settings: Settings
) -> None:
    boundary = "test-boundary"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="nda.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + padding_that_starts_like_a_pdf(
            settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES + 5000
        )
        + f"\r\n--{boundary}--\r\n".encode()
    )

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(body), 4096):
            yield body[start : start + 4096]

    response = await client.post(
        URL,
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    # A streamed body is sent chunked, so there is no Content-Length to check up front.
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert error_code(response) == "file_too_large"


# --- Checks made by the parser -------------------------------------------------------------


async def test_password_protected_pdf_is_rejected(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]], password="secret")

    response = await client.post(URL, files=upload(pdf))

    assert response.status_code == 422
    assert error_code(response) == "file_encrypted"


async def test_damaged_pdf_is_rejected(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."], ["2. Term."]])

    response = await client.post(URL, files=upload(pdf[: len(pdf) // 2]))

    assert response.status_code == 422
    assert error_code(response) == "file_unreadable"


async def test_pdf_without_text_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, files=upload(build_pdf([[], []])))

    assert response.status_code == 422
    assert error_code(response) == "no_text_found"


async def test_pdf_over_the_page_limit_is_rejected(client: AsyncClient) -> None:
    # The test limit is 3 pages.
    pdf = build_pdf([["1."], ["2."], ["3."], ["4."]])

    response = await client.post(URL, files=upload(pdf))

    assert response.status_code == 422
    assert error_code(response) == "too_many_pages"
    assert response.json()["error"]["message"] == "The file has 4 pages. The limit is 3."
