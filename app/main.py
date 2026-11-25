"""Application entry point: `uvicorn app.main:app`."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.middleware import RequestLogMiddleware, UploadSizeLimitMiddleware
from app.api.routes import router
from app.config import Settings, get_settings
from app.db.session import create_engine, create_session_factory
from app.extraction.parsers.factory import build_parser_factory
from app.extraction.prompt import PROMPT_VERSION
from app.llm.factory import build_llm_provider
from app.log_setup import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Runs when the server starts and again when it stops.

    The LLM provider and the database engine are built here and not in `create_app`,
    for two reasons. A wrong LLM configuration stops the server at startup, with a
    message naming the setting. And both hold connections, which are closed on shutdown.

    The database schema is not created here. It comes from the migrations:
    `alembic upgrade head`.
    """
    settings: Settings = app.state.settings
    provider = build_llm_provider(settings)
    engine = create_engine(settings.database_url)
    app.state.llm_provider = provider
    app.state.session_factory = create_session_factory(engine)
    # What a result depends on, written down once. No address and no key: a database
    # URL can hold a password.
    log.info(
        "service_started",
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        llm_reasoning_effort=settings.llm_reasoning_effort,
        prompt_version=PROMPT_VERSION,
        database=engine.dialect.name,
    )
    try:
        yield
    finally:
        await provider.aclose()
        await engine.dispose()
        log.info("service_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or get_settings()
    # First of all, so that everything after it is logged in the same shape. The
    # server has set up its own logging by now, and this takes its place.
    configure_logging(level=settings.log_level)

    app = FastAPI(
        title="Contract Clause Extractor",
        version="1.0.0",
        description="Splits a contract into its clauses. An LLM locates the clauses; "
        "the clause text is always copied from the source document by code.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.parser_factory = build_parser_factory(max_pages=settings.max_pages)

    app.add_middleware(UploadSizeLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    # Added last, so it is the outermost: a request refused by the middleware above
    # gets an id and a log line like every other request.
    app.add_middleware(RequestLogMiddleware)
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
