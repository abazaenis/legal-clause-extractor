"""Application entry point: `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.middleware import UploadSizeLimitMiddleware
from app.api.routes import router
from app.config import Settings, get_settings
from app.extraction.parsers.factory import build_parser_factory
from app.llm.factory import build_llm_provider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs when the server starts and again when it stops.

    The LLM provider is built here and not in `create_app`, for two reasons. A wrong
    LLM configuration stops the server at startup, with a message naming the setting.
    And the provider holds network connections, which are closed on shutdown.
    """
    provider = build_llm_provider(app.state.settings)
    app.state.llm_provider = provider
    try:
        yield
    finally:
        await provider.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or get_settings()

    app = FastAPI(
        title="Contract Clause Extractor",
        version="0.1.0",
        description="Splits a contract into its clauses. An LLM locates the clauses; "
        "the clause text is always copied from the source document by code.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.parser_factory = build_parser_factory(max_pages=settings.max_pages)

    app.add_middleware(UploadSizeLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
