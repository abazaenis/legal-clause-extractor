"""Upload checks on POST /api/extract, up to the point where parsing would start."""

from collections.abc import AsyncIterator

from httpx import AsyncClient, Response

from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.config import Settings

URL = "/api/extract"
PDF_HEADER = b"%PDF-1.7\n"


def pdf_of_size(size: int) -> bytes:
    """Bytes that start like a PDF and are exactly `size` long."""
    return PDF_HEADER + b"0" * (size - len(PDF_HEADER))


def error_code(response: Response) -> str:
    code = response.json()["error"]["code"]
    assert isinstance(code, str)
    return code


def assert_passed_the_upload_checks(response: Response) -> None:
    """A valid upload currently stops at the pipeline step that is not built yet.

    When the pipeline exists, this is the one place to change.
    """
    assert response.status_code == 501
    assert error_code(response) == "not_implemented"


async def test_valid_pdf_passes_the_upload_checks(client: AsyncClient) -> None:
    response = await client.post(
        URL, files={"file": ("nda.pdf", pdf_of_size(200), "application/pdf")}
    )

    assert_passed_the_upload_checks(response)


async def test_pdf_is_accepted_whatever_the_client_calls_it(client: AsyncClient) -> None:
    response = await client.post(URL, files={"file": ("notes.txt", pdf_of_size(200), "text/plain")})

    assert_passed_the_upload_checks(response)


async def test_file_exactly_at_the_limit_is_accepted(
    client: AsyncClient, settings: Settings
) -> None:
    data = pdf_of_size(settings.max_upload_bytes)

    response = await client.post(URL, files={"file": ("nda.pdf", data, "application/pdf")})

    assert_passed_the_upload_checks(response)


async def test_missing_file_field_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, data={"something_else": "value"})

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert "file" in response.json()["error"]["message"]


async def test_empty_file_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, files={"file": ("nda.pdf", b"", "application/pdf")})

    assert response.status_code == 422
    assert error_code(response) == "empty_file"


async def test_content_decides_the_file_type_not_the_name_or_declared_type(
    client: AsyncClient,
) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    response = await client.post(URL, files={"file": ("contract.pdf", png, "application/pdf")})

    assert response.status_code == 415
    assert error_code(response) == "unsupported_file_type"


async def test_word_documents_are_not_supported_yet(client: AsyncClient) -> None:
    docx = b"PK\x03\x04" + b"0" * 100
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    response = await client.post(URL, files={"file": ("contract.docx", docx, media_type)})

    assert response.status_code == 415
    assert error_code(response) == "unsupported_file_type"


async def test_file_one_byte_over_the_limit_is_rejected(
    client: AsyncClient, settings: Settings
) -> None:
    # Small enough to pass the middleware's allowance for multipart wrapping, so this
    # exercises the exact check made after the file has been read.
    data = pdf_of_size(settings.max_upload_bytes + 1)

    response = await client.post(URL, files={"file": ("nda.pdf", data, "application/pdf")})

    assert response.status_code == 413
    assert error_code(response) == "file_too_large"
    # The test limit is 1024 bytes.
    assert response.json()["error"]["message"] == "The file is larger than the limit of 1 KB."


async def test_far_oversized_file_is_rejected(client: AsyncClient, settings: Settings) -> None:
    data = pdf_of_size(settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES + 5000)

    response = await client.post(URL, files={"file": ("nda.pdf", data, "application/pdf")})

    assert "content-length" in response.request.headers
    assert response.status_code == 413
    assert error_code(response) == "file_too_large"


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
        + pdf_of_size(settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES + 5000)
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
