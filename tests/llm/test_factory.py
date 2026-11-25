"""The factory builds the provider named in the settings, or refuses to."""

import json
from typing import Any

import pytest

from app.config import Settings
from app.errors import ConfigurationError, LLMUnavailableError
from app.llm.factory import build_llm_provider
from tests.llm.fake_api import FakeAPI, api_error


def settings(**overrides: Any) -> Settings:
    """Settings with an API key, read from nowhere but the arguments."""
    return Settings(**({"llm_api_key": "test-key-123", "_env_file": None} | overrides))


async def test_deepseek_is_the_default_and_uses_its_own_address() -> None:
    api = FakeAPI()
    provider = build_llm_provider(settings(), http_client=api.http_client())

    response = await provider.complete(system="s", user="u")

    request = api.requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key-123"
    assert json.loads(request.content)["model"] == "deepseek-flash"
    assert response.provider == "deepseek"


async def test_model_limits_and_reasoning_effort_come_from_the_settings() -> None:
    api = FakeAPI()
    provider = build_llm_provider(
        settings(
            llm_model="another-model",
            llm_max_output_tokens=1234,
            llm_reasoning_effort="low",
            llm_timeout_seconds=42,
        ),
        http_client=api.http_client(),
    )

    await provider.complete(system="s", user="u")

    body = json.loads(api.requests[0].content)
    assert body["model"] == "another-model"
    assert body["max_tokens"] == 1234
    assert body["reasoning_effort"] == "low"
    assert api.requests[0].extensions["timeout"]["read"] == 42


async def test_number_of_retries_comes_from_the_settings() -> None:
    api = FakeAPI((503, api_error()))
    provider = build_llm_provider(settings(llm_max_retries=1), http_client=api.http_client())

    with pytest.raises(LLMUnavailableError):
        await provider.complete(system="s", user="u")

    assert len(api.requests) == 2  # the first attempt and one retry


async def test_base_url_setting_replaces_the_deepseek_address() -> None:
    api = FakeAPI()
    provider = build_llm_provider(
        settings(llm_base_url="https://proxy.internal/deepseek"), http_client=api.http_client()
    )

    await provider.complete(system="s", user="u")

    assert str(api.requests[0].url) == "https://proxy.internal/deepseek/chat/completions"


async def test_any_openai_compatible_api_is_reached_through_its_base_url() -> None:
    api = FakeAPI()
    provider = build_llm_provider(
        settings(
            llm_provider="openai_compatible",
            llm_base_url="https://llm.example/v1",
            llm_model="some-other-model",
        ),
        http_client=api.http_client(),
    )

    response = await provider.complete(system="s", user="u")

    assert str(api.requests[0].url) == "https://llm.example/v1/chat/completions"
    assert json.loads(api.requests[0].content)["model"] == "some-other-model"
    assert response.provider == "openai_compatible"


def test_openai_compatible_provider_needs_a_base_url() -> None:
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL is not set"):
        build_llm_provider(settings(llm_provider="openai_compatible"))


def test_unknown_provider_is_refused_with_the_available_names() -> None:
    with pytest.raises(ConfigurationError) as raised:
        build_llm_provider(settings(llm_provider="gpt-9000"))

    assert str(raised.value) == (
        "Unknown LLM_PROVIDER 'gpt-9000'. Available: deepseek, openai_compatible."
    )


def test_missing_api_key_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="LLM_API_KEY is not set"):
        build_llm_provider(settings(llm_api_key=None))
