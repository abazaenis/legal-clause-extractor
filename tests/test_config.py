"""Settings come from the environment, and the API key stays secret."""

import pytest

from app.config import Settings


def test_llm_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "key-from-env")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "key-from-env"
    assert settings.llm_model == "some-model"
    assert settings.llm_reasoning_effort == "low"


def test_api_key_is_masked_wherever_the_settings_are_printed() -> None:
    settings = Settings(llm_api_key="very-secret-key", _env_file=None)

    assert "very-secret-key" not in repr(settings)
    assert "very-secret-key" not in str(settings)
    assert "very-secret-key" not in settings.model_dump_json()


def test_empty_environment_value_counts_as_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # What `LLM_BASE_URL=` and `LLM_API_KEY=` in a copied .env.example amount to.
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")

    settings = Settings(_env_file=None)

    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
