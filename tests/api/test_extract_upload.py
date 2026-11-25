"""POST /api/extract: upload checks, parsing, the LLM call and the repair of its answer."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient, Response

from app.api.dependencies import get_llm_provider
from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.config import Settings
from app.errors import LLMTimeoutError, LLMUnavailableError
from app.extraction.prompt import PROMPT_VERSION, system_prompt
from app.main import create_app
from tests.fakes import FakeLLMProvider
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


def assert_extracted(response: Response) -> None:
    """A valid upload comes back with the places where its parts start."""
    assert response.status_code == 200, response.text
    assert response.json()["clauses"]


@asynccontextmanager
async def client_with(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client for an application built with its own settings, and a fake LLM."""
    app = create_app(settings)
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def extract_with(
    llm: FakeLLMProvider, settings: Settings, pdf: bytes | None = None
) -> Response:
    """Upload a contract to an application that talks to this LLM."""
    app = create_app(settings)
    app.dependency_overrides[get_llm_provider] = lambda: llm
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(URL, files=upload(pdf or build_pdf([["1. Definitions."]])))


def two_page_contract() -> bytes:
    """Seven lines. The header and footer lines are numbered 1, 4, 5 and 7."""
    return build_pdf(
        [["1. Definitions.", "Some text."], ["2. Term."]],
        header="ACME CONFIDENTIAL",
        footer="Page {page} of {pages}",
    )


# --- Valid uploads -----------------------------------------------------------------------


async def test_valid_pdf_comes_back_with_its_clause_starts(settings: Settings) -> None:
    metadata = {
        "title": "SERVICES AGREEMENT",
        "contract_type": "service_agreement",
        "effective_date": "March 1, 2021",
        "parties": [{"name": "Acme Ltd", "role": "Supplier"}, {"name": None, "role": "Customer"}],
    }
    llm = FakeLLMProvider(
        {
            "metadata": metadata,
            "clauses": [
                {"line": 2, "type": "definitions", "number": "1.", "title": "Definitions"},
                {"line": 6, "type": "term_and_termination", "number": "2", "title": "Term"},
            ],
        }
    )

    response = await extract_with(llm, settings, two_page_contract())

    assert response.status_code == 200
    assert response.json() == {
        "metadata": metadata,
        "clauses": [
            {"line": 2, "type": "definitions", "number": "1", "title": "Definitions"},
            {"line": 6, "type": "term_and_termination", "number": "2", "title": "Term"},
        ],
        "warnings": [],
        "llm": {
            "provider": "fake",
            "model": "fake-model",
            "prompt_version": PROMPT_VERSION,
            "input_tokens": 1000,
            "output_tokens": 50,
            "duration_ms": 7,
        },
    }


async def test_llm_receives_the_instructions_and_the_contract_as_numbered_lines(
    client: AsyncClient, llm: FakeLLMProvider
) -> None:
    await client.post(URL, files=upload(two_page_contract()))

    # The header and footer lines are left out, and their numbers stay unused.
    assert llm.calls == [
        {
            "system": system_prompt(),
            "user": (
                "<document>\n"
                "[page 1]\n"
                "2| 1. Definitions.\n"
                "3| Some text.\n"
                "[page 2]\n"
                "6| 2. Term.\n"
                "</document>"
            ),
        }
    ]


async def test_pdf_is_accepted_whatever_the_client_calls_it(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]])

    response = await client.post(URL, files=upload(pdf, "notes.txt", "text/plain"))

    assert_extracted(response)


async def test_file_exactly_at_the_size_limit_is_accepted() -> None:
    pdf = build_pdf([["1. Definitions."]])

    async with client_with(Settings(max_upload_bytes=len(pdf), _env_file=None)) as client:
        response = await client.post(URL, files=upload(pdf))

    assert_extracted(response)


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


# --- The LLM call --------------------------------------------------------------------------


async def test_rejected_upload_never_reaches_the_llm(
    client: AsyncClient, llm: FakeLLMProvider
) -> None:
    await client.post(URL, files=upload(b"Plain text, not a PDF."))
    await client.post(URL, files=upload(build_pdf([[], []])))

    # A file that fails a check costs nothing.
    assert llm.calls == []


async def test_unavailable_llm_is_a_bad_gateway(settings: Settings) -> None:
    llm = FakeLLMProvider(LLMUnavailableError("The provider is unavailable."))

    response = await extract_with(llm, settings)

    assert response.status_code == 502
    assert error_code(response) == "llm_unavailable"


async def test_llm_timeout_is_a_gateway_timeout(settings: Settings) -> None:
    llm = FakeLLMProvider(LLMTimeoutError("The provider did not answer."))

    response = await extract_with(llm, settings)

    assert response.status_code == 504
    assert error_code(response) == "llm_timeout"


# --- The LLM's answer ----------------------------------------------------------------------


async def test_small_problem_in_the_answer_is_repaired_and_reported(settings: Settings) -> None:
    llm = FakeLLMProvider(
        {
            "clauses": [
                {"line": 2, "type": "definitions", "number": "1", "title": "Definitions"},
                {"line": 6, "type": "duration", "number": "2", "title": "Term"},
            ]
        }
    )

    response = await extract_with(llm, settings, two_page_contract())

    assert response.status_code == 200
    body = response.json()
    assert [(clause["line"], clause["type"]) for clause in body["clauses"]] == [
        (2, "definitions"),
        (6, "other"),
    ]
    assert body["warnings"] == [
        {
            "code": "unknown_clause_type",
            "message": 'The clause start on line 6 had the unknown type "duration" and was '
            'labelled "other".',
        }
    ]


async def test_answer_that_is_not_json_is_a_bad_gateway(settings: Settings) -> None:
    llm = FakeLLMProvider("Sure! Here are the clauses you asked for.")

    response = await extract_with(llm, settings)

    assert response.status_code == 502
    assert error_code(response) == "llm_invalid_response"
    # The model's words are not passed on to the caller.
    assert "Sure!" not in response.text
    # And the LLM is not asked a second time.
    assert len(llm.calls) == 1


async def test_answer_that_needs_too_many_fixes_is_a_bad_gateway(settings: Settings) -> None:
    # Line 4 is a page footer, and the document has no line 40.
    llm = FakeLLMProvider({"clauses": [{"line": 2, "type": "other"}, {"line": 4}, {"line": 40}]})

    response = await extract_with(llm, settings, two_page_contract())

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "llm_invalid_response",
            "message": "The LLM's answer could not be used: 2 of its 3 clause starts needed "
            "correcting.",
        }
    }
    assert len(llm.calls) == 1
