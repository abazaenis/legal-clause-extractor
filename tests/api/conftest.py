"""Fixtures for tests that call the API in-process, without a running server."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_llm_provider
from app.config import Settings
from app.main import create_app
from tests.fakes import FakeLLMProvider


@pytest.fixture
def settings() -> Settings:
    """Settings with limits small enough for a test to exceed cheaply.

    `_env_file=None` keeps a developer's local .env file out of the tests.
    """
    return Settings(max_upload_bytes=64 * 1024, max_pages=3, _env_file=None)


@pytest.fixture
def llm() -> FakeLLMProvider:
    """The LLM the application under test talks to. No test reaches a real provider."""
    return FakeLLMProvider()


@pytest.fixture
def app(settings: Settings, llm: FakeLLMProvider) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_llm_provider] = lambda: llm
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
