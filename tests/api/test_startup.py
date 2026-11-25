"""What happens when the server starts and stops."""

import pytest

from app import main
from app.config import Settings
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
