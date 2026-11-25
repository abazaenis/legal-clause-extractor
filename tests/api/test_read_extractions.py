"""GET /api/extractions and GET /api/extractions/{document_id}: reading stored results."""

import uuid
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from tests.fakes import FakeLLMProvider
from tests.helpers import AppBuilder, build_pdf

EXTRACT = "/api/extract"
LIST = "/api/extractions"


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def upload(pdf: bytes, name: str = "contract.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, pdf, "application/pdf")}


async def store(client: AsyncClient, *lines: str, name: str = "contract.pdf") -> dict[str, Any]:
    """Upload a one-page contract with these lines, and return what the API answered.

    Every call needs lines of its own: the same file again is the same document.
    """
    response = await client.post(EXTRACT, files=upload(build_pdf([list(lines)]), name))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def page_of(client: AsyncClient, **params: int) -> dict[str, Any]:
    response = await client.get(LIST, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(page: dict[str, Any]) -> list[str]:
    return [item["document_id"] for item in page["items"]]


# --- One extraction ----------------------------------------------------------------------------


async def test_stored_extraction_is_read_back_as_post_returned_it(client: AsyncClient) -> None:
    created = await client.post(EXTRACT, files=upload(build_pdf([["1. Definitions.", "Text."]])))

    # The Location header of the upload says where the result can be read.
    read = await client.get(created.headers["location"])

    assert read.status_code == 200
    assert read.json() == created.json()
    assert read.json()["clauses"][0]["text"] == "1. Definitions.\nText."


async def test_unknown_document_id_is_not_found(client: AsyncClient) -> None:
    await store(client, "1. Definitions.")
    document_id = uuid.uuid7()

    response = await client.get(f"{LIST}/{document_id}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "extraction_not_found",
            "message": f"No extraction is stored for the document id {document_id}.",
        }
    }


# --- The list ----------------------------------------------------------------------------------


async def test_list_shows_summaries_newest_first(client: AsyncClient) -> None:
    first = await store(client, "1. Definitions.", name="first.pdf")
    second = await store(client, "1. Term.", name="second.pdf")

    page = await page_of(client)

    assert page["total"] == 2
    assert ids(page) == [second["document_id"], first["document_id"]]
    # A summary is the full result without its clauses, warnings, page noise and LLM facts.
    assert page["items"][1] == {
        "document_id": first["document_id"],
        "created_at": first["created_at"],
        "file": first["file"],
        "metadata": first["metadata"],
        "clause_count": 1,
        "warning_count": 0,
    }
    assert page["items"][0]["file"]["filename"] == "second.pdf"


async def test_summary_counts_the_clauses_and_the_warnings(
    build_app: AppBuilder, settings: Settings
) -> None:
    # Three parts, and one type the code does not know: one warning.
    llm = FakeLLMProvider(
        {
            "clauses": [
                {"line": 1, "type": "preamble"},
                {"line": 2, "type": "definitions"},
                {"line": 3, "type": "duration"},
            ]
        }
    )

    async with client_for(build_app(settings, llm)) as client:
        await store(client, "SERVICES AGREEMENT", "1. Definitions.", "2. Term.")
        page = await page_of(client)

    (item,) = page["items"]
    assert (item["clause_count"], item["warning_count"]) == (3, 1)


async def test_list_is_cut_into_pages_by_limit_and_offset(client: AsyncClient) -> None:
    stored = [await store(client, f"{number}. Clause number {number}.") for number in range(1, 6)]
    newest_first = [body["document_id"] for body in reversed(stored)]

    first = await page_of(client, limit=2)
    second = await page_of(client, limit=2, offset=2)
    third = await page_of(client, limit=2, offset=4)
    beyond = await page_of(client, limit=2, offset=6)

    # The pages do not overlap, and together they hold every extraction.
    assert [ids(page) for page in (first, second, third, beyond)] == [
        newest_first[:2],
        newest_first[2:4],
        newest_first[4:],
        [],
    ]
    # Every page says how many there are in all, and repeats what was asked for.
    assert [page["total"] for page in (first, second, third, beyond)] == [5, 5, 5, 5]
    assert (second["limit"], second["offset"]) == (2, 2)


async def test_uploading_a_file_again_leaves_the_list_as_it_was(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]])
    older = (await client.post(EXTRACT, files=upload(pdf))).json()
    newer = await store(client, "1. Term.")

    await client.post(EXTRACT, files=upload(pdf))

    # The stored result is returned, not stored again: same order, same total.
    page = await page_of(client)
    assert ids(page) == [newer["document_id"], older["document_id"]]
    assert page["total"] == 2


# --- The documentation ---------------------------------------------------------------------------
