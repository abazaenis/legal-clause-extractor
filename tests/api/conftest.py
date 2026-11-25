"""Fixtures for tests that call the API in-process, without a running server."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.dependencies import get_llm_provider, get_session_factory
from app.config import Settings
from app.db.session import create_session_factory
from app.main import create_app
from tests.fakes import FakeLLMProvider
from tests.helpers import AppBuilder


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
def build_app(engine: AsyncEngine) -> AppBuilder:
    """Builds an application that talks to a fake LLM and to this test's own database.

    The in-process client does not run the server's startup, which is where the real
    provider and the real database engine are built. Both are injected here instead.
    """
    session_factory = create_session_factory(engine)

    def build(settings: Settings, llm: FakeLLMProvider) -> FastAPI:
        app = create_app(settings)
        app.dependency_overrides[get_llm_provider] = lambda: llm
        app.dependency_overrides[get_session_factory] = lambda: session_factory
        return app

    return build


@pytest.fixture
def app(settings: Settings, llm: FakeLLMProvider, build_app: AppBuilder) -> FastAPI:
    return build_app(settings, llm)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
