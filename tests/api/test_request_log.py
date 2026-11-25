"""Every request gets an id and one log line, and no log line carries contract content."""

import re
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from app.api.middleware import MULTIPART_OVERHEAD_BYTES
from app.config import Settings
from tests.fakes import FakeLLMProvider
from tests.helpers import AppBuilder, build_pdf

OUR_OWN_ID = re.compile(r"[0-9a-f]{32}")


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def upload(pdf: bytes, name: str = "contract.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, pdf, "application/pdf")}


# --- The request id --------------------------------------------------------------------------


async def test_every_response_carries_a_request_id_of_its_own(client: AsyncClient) -> None:
    answered = await client.get("/api/extractions")
    unknown_path = await client.get("/no-such-path")
    refused = await client.get("/api/extractions", params={"limit": "0"})

    ids = [response.headers["x-request-id"] for response in (answered, unknown_path, refused)]
    assert all(OUR_OWN_ID.fullmatch(request_id) for request_id in ids)
    assert len(set(ids)) == 3


async def test_request_stopped_for_its_size_has_a_request_id_too(
    client: AsyncClient, settings: Settings
) -> None:
    # This one is answered by the size limit, before the application sees it.
    too_large = b"%PDF-1.7\n" + b"0" * (settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES)

    response = await client.post("/api/extract", files=upload(too_large))

    assert response.status_code == 413
    assert OUR_OWN_ID.fullmatch(response.headers["x-request-id"])


async def test_request_id_sent_by_the_caller_is_kept(client: AsyncClient) -> None:
    response = await client.get("/api/extractions", headers={"X-Request-ID": "checkout-7f3a.42"})

    assert response.headers["x-request-id"] == "checkout-7f3a.42"


@pytest.mark.parametrize(
    "sent",
    [
        pytest.param("two words", id="a space"),
        pytest.param('id"}, {"event": "forged', id="characters that could forge a log line"),
        pytest.param("x" * 65, id="too long"),
        pytest.param("", id="empty"),
    ],
)
async def test_request_id_that_does_not_look_like_one_is_replaced(
    client: AsyncClient, sent: str
) -> None:
    response = await client.get("/api/extractions", headers={"X-Request-ID": sent})

    assert OUR_OWN_ID.fullmatch(response.headers["x-request-id"])


# --- The log lines of a request ----------------------------------------------------------------


async def test_one_line_is_logged_when_a_request_is_done(client: AsyncClient) -> None:
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = await client.get("/api/extractions", params={"limit": "5"})

    (line,) = [entry for entry in logs if entry["event"] == "request_finished"]
    assert line == {
        "event": "request_finished",
        "log_level": "info",
        "request_id": response.headers["x-request-id"],
        "method": "GET",
        # The path, without the query string.
        "path": "/api/extractions",
        "status_code": 200,
        "duration_ms": line["duration_ms"],
    }
    assert isinstance(line["duration_ms"], int)


async def test_status_is_logged_as_a_plain_number_for_every_kind_of_response(
    client: AsyncClient,
) -> None:
    with capture_logs() as logs:
        await client.get("/api/extractions")
        await client.get("/api/extractions", params={"limit": "0"})
        await client.get("/no-such-path")
        await client.post("/api/extract", files=upload(b"Not a PDF at all."))

    statuses = [entry["status_code"] for entry in logs if entry["event"] == "request_finished"]
    assert statuses == [200, 422, 404, 415]
    # Not an `HTTPStatus`, which a text log would write out by its name.
    assert all(type(status) is int for status in statuses)


async def test_every_line_logged_for_a_request_carries_its_id(client: AsyncClient) -> None:
    pdf = build_pdf([["1. Definitions."]])

    with capture_logs(processors=[merge_contextvars]) as logs:
        first = await client.post("/api/extract", files=upload(pdf))
        again = await client.post("/api/extract", files=upload(pdf))

    def events_of(request_id: str) -> list[str]:
        return [entry["event"] for entry in logs if entry["request_id"] == request_id]

    assert events_of(first.headers["x-request-id"]) == [
        "document_parsed",
        "extraction_finished",
        "extraction_stored",
        "request_finished",
    ]
    # The same file again: nothing is parsed and no LLM is called.
    assert events_of(again.headers["x-request-id"]) == [
        "extraction_served_from_storage",
        "request_finished",
    ]


async def test_refused_request_is_logged_with_its_code_and_not_its_message(
    client: AsyncClient,
) -> None:
    with capture_logs() as logs:
        await client.post("/api/extract", files=upload(b"Not a PDF at all."))

    refused, finished = logs
    assert refused == {
        "event": "request_refused",
        "log_level": "warning",
        "code": "unsupported_file_type",
        "status_code": 415,
    }
    assert (finished["event"], finished["status_code"]) == ("request_finished", 415)


async def test_requests_refused_before_the_endpoint_runs_are_logged_too(
    client: AsyncClient, settings: Settings
) -> None:
    too_large = b"%PDF-1.7\n" + b"0" * (settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES)

    with capture_logs() as logs:
        await client.post("/api/extract", files=upload(too_large))
        await client.get("/no-such-path")
        await client.delete("/api/extractions")

    refused = [
        (entry["code"], entry["status_code"])
        for entry in logs
        if entry["event"] == "request_refused"
    ]
    assert refused == [("file_too_large", 413), ("not_found", 404), ("method_not_allowed", 405)]


async def test_failure_of_the_llm_is_logged_as_an_error(
    build_app: AppBuilder, settings: Settings
) -> None:
    # The application is built first: building it sets up logging, which would end the capture.
    app = build_app(settings, FakeLLMProvider("Sure! Here are the clauses."))

    with capture_logs() as logs:
        async with client_for(app) as client:
            await client.post("/api/extract", files=upload(build_pdf([["1. Definitions."]])))

    (refused,) = [entry for entry in logs if entry["event"] == "request_refused"]
    assert refused == {
        "event": "request_refused",
        "log_level": "error",
        "code": "llm_invalid_response",
        "status_code": 502,
    }


async def test_unhandled_error_is_logged_with_its_traceback_and_answered_with_the_id(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    @app.get("/boom")
    async def boom() -> None:
        clause_text = "CONFIDENTIAL CLAUSE TEXT"
        raise RuntimeError(f"Failed after {len(clause_text)} characters.")

    # By default the test transport re-raises application errors. Turning that off
    # shows what a real client would receive.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert OUR_OWN_ID.fullmatch(request_id)

    output = capsys.readouterr().out
    error_line = next(line for line in output.splitlines() if "unhandled_error" in line)
    request_line = next(line for line in output.splitlines() if "request_finished" in line)
    # Both lines can be found by the id the caller was given.
    assert "[error" in error_line
    assert f"request_id={request_id}" in error_line
    assert f"request_id={request_id}" in request_line
    assert "status_code=500" in request_line
    # The traceback says where it failed and why, and not what the variables held.
    assert "in boom" in output
    assert "RuntimeError: Failed after 24 characters." in output
    assert "CONFIDENTIAL" not in output


# --- What never reaches the logs ---------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            {
                "metadata": {
                    "title": "ZEBRA HOLDINGS MASTER AGREEMENT",
                    "parties": [{"name": "Zebra Holdings Ltd", "role": "Zebra"}],
                },
                "clauses": [
                    {"line": 1, "type": "preamble"},
                    # A type and a title that are not there: two warnings that quote them.
                    {"line": 2, "type": "zebra_duties", "number": "1", "title": "Zebra Duties"},
                ],
            },
            id="an extraction with warnings",
        ),
        pytest.param("Sure! The ZEBRA HOLDINGS agreement has two clauses.", id="a refused answer"),
    ],
)
async def test_no_contract_content_reaches_the_logs(
    build_app: AppBuilder,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    answer: dict[str, Any] | str,
) -> None:
    pdf = build_pdf(
        [["ZEBRA HOLDINGS MASTER AGREEMENT", "1. Definitions. Zebra Holdings Ltd pays Quagga."]]
    )

    async with client_for(build_app(settings, FakeLLMProvider(answer))) as client:
        response = await client.post("/api/extract", files=upload(pdf, "zebra-holdings-nda.pdf"))
        await client.get("/api/extractions")

    logged = capsys.readouterr().out
    # The request was logged, in full.
    assert "request_finished" in logged
    assert response.headers["x-request-id"] in logged
    # Not the text, not the model's answer, not the file name, not the warning messages.
    assert "zebra" not in logged.lower()
    assert "quagga" not in logged.lower()
