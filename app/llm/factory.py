"""Builds the LLM provider named in the settings."""

from collections.abc import Callable

import httpx2
from openai import AsyncOpenAI

from app.config import Settings
from app.errors import ConfigurationError
from app.llm.adapters.openai_compatible import OpenAICompatibleProvider
from app.llm.base import LLMProvider

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

type ProviderBuilder = Callable[[Settings, httpx2.AsyncClient | None], LLMProvider]


def build_llm_provider(
    settings: Settings, http_client: httpx2.AsyncClient | None = None
) -> LLMProvider:
    """Return the provider chosen by `LLM_PROVIDER`.

    Raises `ConfigurationError` for an unknown name or a missing setting, so a wrong
    configuration stops the server at startup, not at the first upload.

    `http_client` replaces the SDK's own HTTP client. The tests use it to answer
    requests themselves, without any network.
    """
    try:
        build = _BUILDERS[settings.llm_provider]
    except KeyError:
        available = ", ".join(sorted(_BUILDERS))
        raise ConfigurationError(
            f"Unknown LLM_PROVIDER {settings.llm_provider!r}. Available: {available}."
        ) from None
    return build(settings, http_client)


def _build_deepseek(settings: Settings, http_client: httpx2.AsyncClient | None) -> LLMProvider:
    return _openai_compatible(
        settings, http_client, base_url=settings.llm_base_url or DEEPSEEK_BASE_URL
    )


def _build_openai_compatible(
    settings: Settings, http_client: httpx2.AsyncClient | None
) -> LLMProvider:
    if not settings.llm_base_url:
        raise ConfigurationError(
            "LLM_BASE_URL is not set. The openai_compatible provider needs the address of the API."
        )
    return _openai_compatible(settings, http_client, base_url=settings.llm_base_url)


def _openai_compatible(
    settings: Settings, http_client: httpx2.AsyncClient | None, *, base_url: str
) -> LLMProvider:
    if settings.llm_api_key is None:
        raise ConfigurationError(
            "LLM_API_KEY is not set. Put the provider's API key in the environment or in .env."
        )
    client = AsyncOpenAI(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        http_client=http_client,
    )
    return OpenAICompatibleProvider(
        name=settings.llm_provider,
        client=client,
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    )


# Supporting a provider with its own API takes one adapter class and one entry here.
_BUILDERS: dict[str, ProviderBuilder] = {
    "deepseek": _build_deepseek,
    "openai_compatible": _build_openai_compatible,
}
