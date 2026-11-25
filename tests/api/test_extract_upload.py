"""POST /api/extract: from the uploaded file to the stored result."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.config import Settings
from app.errors import LLMTimeoutError, LLMUnavailableError
from app.extraction.prompt import PROMPT_VERSION, system_prompt
from tests.fakes import FakeLLMProvider
from tests.helpers import AppBuilder, build_docx, build_pdf, docx_paragraph, docx_table

URL = "/api/extract"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
UploadField = dict[str, tuple[str, bytes, str]]


def upload(
    data: bytes, filename: str = "nda.pdf", media_type: str = "application/pdf"
) -> UploadField:
    return {"file": (filename, data, media_type)}


async def post(app: FastAPI, pdf: bytes) -> Response:
    """Upload a contract to this application."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(URL, files=upload(pdf))


def padding_that_starts_like_a_pdf(size: int) -> bytes:
    """Exactly `size` bytes with a PDF header. Enough for the size checks, which run
    before anything tries to read the file."""
    header = b"%PDF-1.7\n"
    return header + b"0" * (size - len(header))


def error_code(response: Response) -> str:
    code = response.json()["error"]["code"]
    assert isinstance(code, str)
    return code


def contract() -> bytes:
    """A contract of two pages and eleven lines.

    The header and footer lines are numbered 1, 7, 8 and 11. `ANSWER` is what a
    model that reads it well returns.
    """
    return build_pdf(
        [
            [
                "SERVICES AGREEMENT",
                "between Acme Ltd (the Supplier) and the Customer,",
                "effective March 1, 2021.",
                "1. Definitions.",
                "Some text.",
            ],
            ["2. Term.", "One year."],
        ],
        header="ACME CONFIDENTIAL",
        footer="Page {page} of {pages}",
    )


ANSWER: dict[str, Any] = {
    "metadata": {
        "title": "SERVICES AGREEMENT",
        "contract_type": "service_agreement",
        "effective_date": "March 1, 2021",
        "parties": [{"name": "Acme Ltd", "role": "Supplier"}, {"name": None, "role": "Customer"}],
    },
    "clauses": [
        {"line": 2, "type": "preamble", "number": None, "title": None},
        {"line": 5, "type": "definitions", "number": "1.", "title": "Definitions"},
        {"line": 9, "type": "term_and_termination", "number": "2", "title": "Term"},
    ],
}


def with_changes(**changes: Any) -> dict[str, Any]:
    """`ANSWER` with some of its top-level values replaced."""
    return ANSWER | changes


# --- A new file ----------------------------------------------------------------------------


async def test_new_contract_is_extracted_stored_and_returned(
    build_app: AppBuilder, settings: Settings
) -> None:
    pdf = contract()
    before = datetime.now(UTC)

    response = await post(build_app(settings, FakeLLMProvider(ANSWER)), pdf)

    assert response.status_code == 201
    body = response.json()
    assert response.headers["location"] == f"/api/extractions/{body['document_id']}"
    assert uuid.UUID(body["document_id"]).version == 7
    assert before <= datetime.fromisoformat(body["created_at"]) <= datetime.now(UTC)
    assert body == {
        "document_id": body["document_id"],
        "created_at": body["created_at"],
        "file": {
            "filename": "nda.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(pdf),
            "page_count": 2,
            "sha256": hashlib.sha256(pdf).hexdigest(),
        },
        "metadata": {
            "title": "SERVICES AGREEMENT",
            "contract_type": "service_agreement",
            # Read by code from "March 1, 2021".
            "effective_date": "2021-03-01",
            "parties": [
                {"name": "Acme Ltd", "role": "Supplier"},
                {"name": None, "role": "Customer"},
            ],
        },
        "clauses": [
            {
                "position": 0,
                "type": "preamble",
                "number": None,
                "title": None,
                "text": "SERVICES AGREEMENT\n"
                "between Acme Ltd (the Supplier) and the Customer,\n"
                "effective March 1, 2021.",
                "start_line": 2,
                "end_line": 4,
                "start_page": 1,
                "end_page": 1,
            },
            {
                "position": 1,
                "type": "definitions",
                "number": "1",
                "title": "Definitions",
                "text": "1. Definitions.\nSome text.",
                "start_line": 5,
                "end_line": 6,
                "start_page": 1,
                "end_page": 1,
            },
            {
                "position": 2,
                "type": "term_and_termination",
                "number": "2",
                "title": "Term",
                "text": "2. Term.\nOne year.",
                "start_line": 9,
                "end_line": 10,
                "start_page": 2,
                "end_page": 2,
            },
        ],
        "warnings": [],
        "page_noise": [
            {"text": "ACME CONFIDENTIAL", "occurrences": 2},
            {"text": "Page # of #", "occurrences": 2},
        ],
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
    build_app: AppBuilder, settings: Settings
) -> None:
    llm = FakeLLMProvider(ANSWER)

    await post(build_app(settings, llm), contract())

    # The header and footer lines are left out, and their numbers stay unused.
    assert llm.calls == [
        {
            "system": system_prompt(),
            "user": (
                "<document>\n"
                "[page 1]\n"
                "2| SERVICES AGREEMENT\n"
                "3| between Acme Ltd (the Supplier) and the Customer,\n"
                "4| effective March 1, 2021.\n"
                "5| 1. Definitions.\n"
                "6| Some text.\n"
                "[page 2]\n"
                "9| 2. Term.\n"
                "10| One year.\n"
                "</document>"
            ),
        }
    ]


async def test_pdf_is_accepted_whatever_the_client_calls_it(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]])

    response = await client.post(URL, files=upload(pdf, "notes.txt", "text/plain"))

    assert response.status_code == 201
    # The name is kept as given. The type is what the content says it is.
    assert response.json()["file"]["filename"] == "notes.txt"
    assert response.json()["file"]["content_type"] == "application/pdf"


async def test_file_exactly_at_the_size_limit_is_accepted(build_app: AppBuilder) -> None:
    pdf = build_pdf([["1. Definitions."]])
    settings = Settings(max_upload_bytes=len(pdf), _env_file=None)

    response = await post(build_app(settings, FakeLLMProvider()), pdf)

    assert response.status_code == 201


# --- A Word document ---------------------------------------------------------------------------


async def test_word_document_is_extracted_like_a_pdf_only_without_pages(
    build_app: AppBuilder, settings: Settings
) -> None:
    docx = build_docx(
        docx_paragraph("SERVICES AGREEMENT")
        + docx_paragraph("between Acme Ltd (the Supplier) and the Customer.")
        + docx_paragraph()
        + docx_paragraph("1. Definitions.")
        + docx_table(["Notice to Acme:", "1 Main Street"])
        + docx_paragraph("2. Term.")
    )
    llm = FakeLLMProvider(
        {
            "metadata": {"title": "SERVICES AGREEMENT", "contract_type": "service_agreement"},
            "clauses": [
                {"line": 1, "type": "preamble"},
                {"line": 3, "type": "definitions", "number": "1", "title": "Definitions"},
                {"line": 5, "type": "term_and_termination", "number": "2", "title": "Term"},
            ],
        }
    )
    app = build_app(settings, llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, files=upload(docx, "msa.docx", DOCX_MEDIA_TYPE))
        again = await client.post(URL, files=upload(docx, "msa.docx", DOCX_MEDIA_TYPE))

    assert response.status_code == 201
    body = response.json()
    assert body["file"] == {
        "filename": "msa.docx",
        "content_type": DOCX_MEDIA_TYPE,
        "size_bytes": len(docx),
        # A Word document has no pages.
        "page_count": None,
        "sha256": hashlib.sha256(docx).hexdigest(),
    }
    assert [
        (clause["number"], clause["text"], clause["start_line"], clause["end_line"])
        for clause in body["clauses"]
    ] == [
        (None, "SERVICES AGREEMENT\nbetween Acme Ltd (the Supplier) and the Customer.", 1, 2),
        # A paragraph is a line, and so is the row of a table.
        ("1", "1. Definitions.\nNotice to Acme: | 1 Main Street", 3, 4),
        ("2", "2. Term.", 5, 5),
    ]
    assert all(
        clause["start_page"] is None and clause["end_page"] is None for clause in body["clauses"]
    )
    assert (body["warnings"], body["page_noise"]) == ([], [])
    # The LLM saw the paragraphs as numbered lines, with no page markers.
    assert llm.calls[0]["user"] == (
        "<document>\n"
        "1| SERVICES AGREEMENT\n"
        "2| between Acme Ltd (the Supplier) and the Customer.\n"
        "3| 1. Definitions.\n"
        "4| Notice to Acme: | 1 Main Street\n"
        "5| 2. Term.\n"
        "</document>"
    )
    # And the same file again is answered from storage, like a PDF.
    assert again.status_code == 200
    assert again.json() == body
    assert len(llm.calls) == 1


async def test_word_document_that_cannot_be_read_is_rejected(client: AsyncClient) -> None:
    damaged = build_docx("", parts={"word/document.xml": b"<w:document"})

    response = await client.post(URL, files=upload(damaged, "msa.docx", DOCX_MEDIA_TYPE))

    assert response.status_code == 422
    assert error_code(response) == "file_unreadable"


async def test_word_document_without_text_is_rejected(client: AsyncClient) -> None:
    response = await client.post(URL, files=upload(build_docx(docx_paragraph()), "empty.docx"))

    assert response.status_code == 422
    assert error_code(response) == "no_text_found"


async def test_word_document_over_the_page_limit_is_rejected(client: AsyncClient) -> None:
    # The test limit is 3 pages, and 3,000 characters count as a page.
    docx = build_docx(docx_paragraph("x" * 9_001))

    response = await client.post(URL, files=upload(docx, "long.docx"))

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "too_many_pages",
        "message": "The document is about 4 pages long. The limit is 3.",
    }


# --- A file that was extracted before ---------------------------------------------------------


async def test_same_file_again_returns_the_stored_result_without_an_llm_call(
    build_app: AppBuilder, settings: Settings
) -> None:
    pdf = contract()
    llm = FakeLLMProvider(ANSWER)
    app = build_app(settings, llm)
    first = await post(app, pdf)

    again = await post(app, pdf)

    assert (first.status_code, again.status_code) == (201, 200)
    # What comes out of the database is what was returned the first time.
    assert again.json() == first.json()
    assert len(llm.calls) == 1
    # Nothing was created this time, so there is no new location to point at.
    assert "location" not in again.headers


async def test_same_content_under_another_name_is_the_same_document(
    client: AsyncClient, llm: FakeLLMProvider
) -> None:
    pdf = build_pdf([["1. Definitions."]])
    first = await client.post(URL, files=upload(pdf, "nda.pdf"))

    again = await client.post(URL, files=upload(pdf, "copy of nda.pdf"))

    assert again.status_code == 200
    assert again.json()["document_id"] == first.json()["document_id"]
    assert again.json()["file"]["filename"] == "nda.pdf"
    assert len(llm.calls) == 1


async def test_failed_extraction_is_not_stored(build_app: AppBuilder, settings: Settings) -> None:
    pdf = contract()
    llm = FakeLLMProvider(LLMUnavailableError("The provider is unavailable."), ANSWER)
    app = build_app(settings, llm)

    failed = await post(app, pdf)
    retried = await post(app, pdf)

    assert failed.status_code == 502
    # Nothing was stored, so the same file is extracted on the next upload.
    assert retried.status_code == 201
    assert len(llm.calls) == 2


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
    assert response.json()["error"]["message"] == "Only PDF and Word (.docx) files are supported."


async def test_zip_file_that_is_not_a_word_document_is_rejected(client: AsyncClient) -> None:
    # A .docx is a ZIP file, but a ZIP file called .docx is not a Word document.
    archive = b"PK\x03\x04" + b"0" * 100

    response = await client.post(URL, files=upload(archive, "contract.docx", DOCX_MEDIA_TYPE))

    assert response.status_code == 415
    assert error_code(response) == "unsupported_file_type"


async def test_old_word_file_is_rejected_with_advice(client: AsyncClient) -> None:
    doc = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 200

    response = await client.post(URL, files=upload(doc, "contract.doc", "application/msword"))

    assert response.status_code == 415
    assert response.json()["error"] == {
        "code": "unsupported_file_type",
        "message": "Files in the old Office format, such as .doc, are not supported. "
        "Save the document as .docx or as a PDF and upload it again.",
    }


async def test_password_protected_word_file_is_rejected(client: AsyncClient) -> None:
    # A .docx with a password is stored encrypted, in the container of the old formats.
    protected = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + b"\x00" * 64
        + "EncryptedPackage".encode("utf-16-le")
        + b"\x00" * 64
    )

    response = await client.post(URL, files=upload(protected, "contract.docx", DOCX_MEDIA_TYPE))

    assert response.status_code == 422
    assert error_code(response) == "file_encrypted"


async def test_file_one_byte_over_the_size_limit_is_rejected(build_app: AppBuilder) -> None:
    # Small enough to pass the middleware's allowance for multipart wrapping, so this
    # exercises the exact check made after the file has been read.
    pdf = build_pdf([["1. Definitions."]])
    settings = Settings(max_upload_bytes=len(pdf) - 1, _env_file=None)

    response = await post(build_app(settings, FakeLLMProvider()), pdf)

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


async def test_unavailable_llm_is_a_bad_gateway(build_app: AppBuilder, settings: Settings) -> None:
    llm = FakeLLMProvider(LLMUnavailableError("The provider is unavailable."))

    response = await post(build_app(settings, llm), contract())

    assert response.status_code == 502
    assert error_code(response) == "llm_unavailable"


async def test_llm_timeout_is_a_gateway_timeout(build_app: AppBuilder, settings: Settings) -> None:
    llm = FakeLLMProvider(LLMTimeoutError("The provider did not answer."))

    response = await post(build_app(settings, llm), contract())

    assert response.status_code == 504
    assert error_code(response) == "llm_timeout"


# --- The LLM's answer ----------------------------------------------------------------------


async def test_small_problem_in_the_answer_is_repaired_and_reported(
    build_app: AppBuilder, settings: Settings
) -> None:
    clauses = [
        {"line": 2, "type": "preamble"},
        {"line": 5, "type": "definitions", "number": "1", "title": "Definitions"},
        {"line": 9, "type": "duration", "number": "2", "title": "Term"},
    ]

    response = await post(
        build_app(settings, FakeLLMProvider(with_changes(clauses=clauses))), contract()
    )

    assert response.status_code == 201
    body = response.json()
    assert [(clause["start_line"], clause["type"]) for clause in body["clauses"]] == [
        (2, "preamble"),
        (5, "definitions"),
        (9, "other"),
    ]
    assert body["warnings"] == [
        {
            "code": "unknown_clause_type",
            "message": 'The clause start on line 9 had the unknown type "duration" and was '
            'labelled "other".',
        }
    ]


async def test_value_that_is_not_in_the_document_comes_back_empty_with_a_warning(
    build_app: AppBuilder, settings: Settings
) -> None:
    metadata = ANSWER["metadata"] | {"title": "MASTER SERVICES AGREEMENT"}
    clauses = [
        {"line": 2, "type": "preamble"},
        {"line": 5, "type": "definitions", "number": "1", "title": "Defined Terms"},
    ]
    llm = FakeLLMProvider(with_changes(metadata=metadata, clauses=clauses))

    response = await post(build_app(settings, llm), contract())

    assert response.status_code == 201
    body = response.json()
    assert body["metadata"]["title"] is None
    assert (body["clauses"][1]["number"], body["clauses"][1]["title"]) == ("1", None)
    assert [warning["code"] for warning in body["warnings"]] == [
        "clause_title_not_verified",
        "title_not_verified",
    ]
    # The text of the clause is the document's own, whatever the model called it.
    assert body["clauses"][1]["text"] == "1. Definitions.\nSome text.\n2. Term.\nOne year."


async def test_answer_that_is_not_json_is_a_bad_gateway(
    build_app: AppBuilder, settings: Settings
) -> None:
    llm = FakeLLMProvider("Sure! Here are the clauses you asked for.")

    response = await post(build_app(settings, llm), contract())

    assert response.status_code == 502
    assert error_code(response) == "llm_invalid_response"
    # The model's words are not passed on to the caller.
    assert "Sure!" not in response.text
    # And the LLM is not asked a second time.
    assert len(llm.calls) == 1


async def test_answer_that_needs_too_many_fixes_is_a_bad_gateway(
    build_app: AppBuilder, settings: Settings
) -> None:
    # Line 7 is a page footer, and the document has no line 40.
    clauses = [{"line": 2, "type": "other"}, {"line": 7}, {"line": 40}]
    llm = FakeLLMProvider(with_changes(clauses=clauses))

    response = await post(build_app(settings, llm), contract())

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "llm_invalid_response",
            "message": "The LLM's answer could not be used: 2 of its 3 clause starts needed "
            "correcting.",
        }
    }
    assert len(llm.calls) == 1


# --- The documentation -----------------------------------------------------------------------


async def test_openapi_documents_both_outcomes(client: AsyncClient) -> None:
    operation = (await client.get("/openapi.json")).json()["paths"]["/api/extract"]["post"]

    for status in ("200", "201"):
        reference = operation["responses"][status]["content"]["application/json"]["schema"]["$ref"]
        assert reference.endswith("/ExtractionResponse")
    # The file is the only input: there are no query parameters.
    assert "parameters" not in operation
