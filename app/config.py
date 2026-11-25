"""Application settings, read from environment variables and an optional .env file."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field can be overridden by an environment variable of the same name."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `LLM_BASE_URL=` in a .env file means "not set", not an empty address.
        env_ignore_empty=True,
    )

    database_url: str = "sqlite+aiosqlite:///./extractions.db"

    # Largest file accepted by the upload endpoint, in bytes. The default is 20 MB.
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)

    # Longest document accepted, in pages. Only applies to formats that have pages.
    max_pages: int = Field(default=300, gt=0)

    # Which LLM provider to use. The names are registered in app/llm/factory.py.
    llm_provider: str = "deepseek"
    # Required. A secret: it is masked in logs and in the repr of the settings.
    llm_api_key: SecretStr | None = None
    llm_model: str = "deepseek-flash"
    # Replaces the provider's own address. Required for the `openai_compatible` provider.
    llm_base_url: str | None = None
    # Passed to the provider as it is. Unset means the provider's default.
    llm_reasoning_effort: str | None = None
    # Upper limit for one answer, in tokens. An answer that hits it is treated as invalid.
    llm_max_output_tokens: int = Field(default=32_000, gt=0)
    # For one attempt, in seconds. The SDK retries on top of this.
    llm_timeout_seconds: float = Field(default=300, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
