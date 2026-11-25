"""Application settings, read from environment variables and an optional .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every field can be overridden by an environment variable of the same name."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./extractions.db"

    # Largest file accepted by the upload endpoint, in bytes. The default is 20 MB.
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)


@lru_cache
def get_settings() -> Settings:
    """Return the settings, read once per process."""
    return Settings()
