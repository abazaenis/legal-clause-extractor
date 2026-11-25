"""What happens when the server starts and stops."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import main
from app.api.dependencies import LLMProviderDependency
from app.config import Settings
from app.db.session import create_engine
from app.errors import ConfigurationError
from app.llm.base import LLMProvider, LLMResponse
from app.main import create_app


class RecordingProvider:
    """A provider that only remembers whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def complete(self, *, system: str, user: str) -> LLMResponse:
        raise AssertionError("No LLM call is expected at startup.")

    async def aclose(self) -> None:
        self.closed = True


async def test_provider_is_built_at_startup_and_closed_at_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    built_with: list[Settings] = []

    def build(settings: Settings) -> LLMProvider:
        built_with.append(settings)
        return provider

    monkeypatch.setattr(main, "build_llm_provider", build)
    settings = Settings(_env_file=None)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert built_with == [settings]
        assert app.state.llm_provider is provider
        assert not provider.closed

    assert provider.closed


async def test_database_engine_is_built_at_startup_and_closed_at_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engines: list[AsyncEngine] = []

    def build(database_url: str) -> AsyncEngine:
        engines.append(create_engine(database_url))
        return engines[-1]

    monkeypatch.setattr(main, "create_engine", build)
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}"
    app = create_app(
        Settings(llm_api_key="test-key-123", database_url=database_url, _env_file=None)
    )

    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            assert await session.scalar(text("SELECT 1")) == 1
        # The connection went back to the pool and waits there for the next request.
        assert engines[0].sync_engine.pool.checkedin() == 1  # type: ignore[attr-defined]

    assert engines[0].sync_engine.pool.checkedin() == 0  # type: ignore[attr-defined]


async def test_real_provider_is_built_from_the_settings() -> None:
    app = create_app(Settings(llm_api_key="test-key-123", _env_file=None))

    async with app.router.lifespan_context(app):
        assert app.state.llm_provider is not None


async def test_server_refuses_to_start_without_an_api_key() -> None:
    app = create_app(Settings(llm_api_key=None, _env_file=None))

    with pytest.raises(ConfigurationError, match="LLM_API_KEY is not set"):
        async with app.router.lifespan_context(app):
            pass


async def test_server_refuses_to_start_with_an_unknown_provider() -> None:
    app = create_app(Settings(llm_provider="gpt-9000", llm_api_key="test-key-123", _env_file=None))

    with pytest.raises(ConfigurationError, match="Unknown LLM_PROVIDER"):
        async with app.router.lifespan_context(app):
            pass


async def test_routes_get_the_provider_that_was_built_at_startup() -> None:
    app = create_app(Settings(_env_file=None))
    provider = RecordingProvider()
    app.state.llm_provider = provider
    received: list[LLMProvider] = []

    @app.get("/probe")
    async def probe(llm: LLMProviderDependency) -> None:
        received.append(llm)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/probe")

    assert received == [provider]
