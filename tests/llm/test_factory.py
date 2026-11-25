"""The factory builds the provider named in the settings, or refuses to."""

import json
from typing import Any

import pytest

from app.config import Settings
from app.errors import ConfigurationError
from app.llm.factory import build_llm_provider
from tests.llm.fake_api import FakeAPI


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
