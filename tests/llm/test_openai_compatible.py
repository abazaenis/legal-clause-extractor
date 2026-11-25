"""The OpenAI-compatible adapter, tested against a fake HTTP API."""

import json
import logging
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI

from app.errors import (
    AppError,
    LLMInvalidResponseError,
    LLMRequestRejectedError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.adapters.openai_compatible import OpenAICompatibleProvider
from tests.llm.fake_api import FakeAPI, api_error, completion

API_KEY = "test-key-123"
SYSTEM = "You find clause boundaries."
USER = "1| CONFIDENTIAL CONTRACT TEXT"


def provider_for(
    api: FakeAPI, *, max_retries: int = 0, reasoning_effort: str | None = None
) -> OpenAICompatibleProvider:
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url="https://llm.test/v1",
        max_retries=max_retries,
        http_client=api.http_client(),
    )
    return OpenAICompatibleProvider(
        name="test-provider",
        client=client,
        model="requested-model",
        max_output_tokens=4000,
        reasoning_effort=reasoning_effort,
    )


def sent_body(api: FakeAPI) -> dict[str, Any]:
    body = json.loads(api.requests[-1].content)
    assert isinstance(body, dict)
    return body


# --- The request ---------------------------------------------------------------------------


async def test_request_asks_the_configured_model_for_a_json_object() -> None:
    api = FakeAPI()

    await provider_for(api).complete(system=SYSTEM, user=USER)

    request = api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent_body(api) == {
        "model": "requested-model",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }


async def test_reasoning_effort_is_sent_as_configured() -> None:
    api = FakeAPI()

    await provider_for(api, reasoning_effort="max").complete(system=SYSTEM, user=USER)

    assert sent_body(api)["reasoning_effort"] == "max"


# --- The answer ----------------------------------------------------------------------------


async def test_answer_and_token_usage_are_returned() -> None:
    api = FakeAPI((200, completion('{"clauses": []}', prompt_tokens=900, completion_tokens=45)))

    response = await provider_for(api).complete(system=SYSTEM, user=USER)

    assert response.text == '{"clauses": []}'
    assert response.provider == "test-provider"
    # The model the provider says it used, which can differ from the one requested.
    assert response.model == "served-model-2026"
    assert (response.input_tokens, response.output_tokens) == (900, 45)
    assert response.duration_ms >= 0


async def test_answer_text_is_kept_out_of_the_repr() -> None:
    api = FakeAPI((200, completion('{"title": "SECRET AGREEMENT"}')))

    response = await provider_for(api).complete(system=SYSTEM, user=USER)

    assert "SECRET AGREEMENT" not in repr(response)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n ", id="only whitespace"),
        pytest.param(None, id="missing"),
    ],
)
async def test_empty_answer_is_an_invalid_response(content: str | None) -> None:
    # DeepSeek documents this: in JSON mode the content can come back blank.
    api = FakeAPI((200, completion(content)))

    with pytest.raises(LLMInvalidResponseError, match="empty answer"):
        await provider_for(api).complete(system=SYSTEM, user=USER)


async def test_answer_cut_off_at_the_output_limit_is_an_invalid_response() -> None:
    api = FakeAPI((200, completion('{"clauses": [{"line": 1', finish_reason="length")))

    with pytest.raises(LLMInvalidResponseError, match="cut off"):
        await provider_for(api).complete(system=SYSTEM, user=USER)


# --- Failures --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(429, LLMUnavailableError, id="rate limited"),
        pytest.param(500, LLMUnavailableError, id="server error"),
        pytest.param(503, LLMUnavailableError, id="overloaded"),
        pytest.param(400, LLMRequestRejectedError, id="bad request"),
        pytest.param(401, LLMRequestRejectedError, id="wrong api key"),
        pytest.param(402, LLMRequestRejectedError, id="no balance"),
        pytest.param(404, LLMRequestRejectedError, id="unknown model"),
        pytest.param(422, LLMRequestRejectedError, id="invalid parameters"),
    ],
)
async def test_error_status_becomes_an_application_error(
    status: int, expected: type[AppError]
) -> None:
    api = FakeAPI((status, api_error()))

    with pytest.raises(expected):
        await provider_for(api).complete(system=SYSTEM, user=USER)


async def test_timeout_becomes_a_timeout_error() -> None:
    api = FakeAPI(httpx2.ReadTimeout("The provider took too long."))

    with pytest.raises(LLMTimeoutError):
        await provider_for(api).complete(system=SYSTEM, user=USER)


async def test_network_failure_becomes_unavailable() -> None:
    api = FakeAPI(httpx2.ConnectError("Connection refused."))

    with pytest.raises(LLMUnavailableError):
        await provider_for(api).complete(system=SYSTEM, user=USER)


async def test_temporary_failure_is_retried_by_the_sdk() -> None:
    api = FakeAPI((429, api_error()), (503, api_error()), (200, completion('{"ok": true}')))

    response = await provider_for(api, max_retries=2).complete(system=SYSTEM, user=USER)

    assert response.text == '{"ok": true}'
    assert len(api.requests) == 3


async def test_retries_stop_at_the_configured_number() -> None:
    api = FakeAPI((503, api_error()))

    with pytest.raises(LLMUnavailableError):
        await provider_for(api, max_retries=2).complete(system=SYSTEM, user=USER)

    assert len(api.requests) == 3  # the first attempt and two retries


async def test_rejected_request_is_not_retried() -> None:
    api = FakeAPI((401, api_error()))

    with pytest.raises(LLMRequestRejectedError):
        await provider_for(api, max_retries=2).complete(system=SYSTEM, user=USER)

    assert len(api.requests) == 1


# --- What must not leak ----------------------------------------------------------------------


async def test_provider_error_details_do_not_reach_the_caller() -> None:
    api = FakeAPI((401, api_error(f"Incorrect API key provided: {API_KEY}")))

    with pytest.raises(LLMRequestRejectedError) as raised:
        await provider_for(api).complete(system=SYSTEM, user=USER)

    assert API_KEY not in raised.value.message


async def test_prompts_answers_and_keys_are_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = FakeAPI(
        (200, completion('{"title": "SECRET AGREEMENT"}')),
        (401, api_error(f"Incorrect API key provided: {API_KEY}")),
    )
    provider = provider_for(api)

    with caplog.at_level(logging.DEBUG, logger="app"):
        await provider.complete(system=SYSTEM, user=USER)
        with pytest.raises(LLMRequestRejectedError):
            await provider.complete(system=SYSTEM, user=USER)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    # The useful facts are there.
    assert "input_tokens=120 output_tokens=30" in logged
    assert "status=401" in logged
    # Contract text, the model's answer and the key are not.
    assert "CONFIDENTIAL CONTRACT TEXT" not in logged
    assert "SECRET AGREEMENT" not in logged
    assert API_KEY not in logged


# --- Shutdown --------------------------------------------------------------------------------


async def test_closing_the_provider_closes_its_connections() -> None:
    api = FakeAPI()
    http_client = api.http_client()
    client = AsyncOpenAI(api_key=API_KEY, base_url="https://llm.test/v1", http_client=http_client)
    provider = OpenAICompatibleProvider(
        name="test-provider", client=client, model="requested-model", max_output_tokens=4000
    )

    await provider.aclose()

    assert http_client.is_closed
