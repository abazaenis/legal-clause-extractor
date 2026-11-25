"""Application entry point: `uvicorn app.main:app`."""

from fastapi import FastAPI

from app.api.error_handlers import register_error_handlers
from app.api.middleware import UploadSizeLimitMiddleware
from app.api.routes import router
from app.config import Settings, get_settings
from app.extraction.parsers.factory import build_parser_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings; production reads the environment."""
    settings = settings or get_settings()

    app = FastAPI(
        title="Contract Clause Extractor",
        version="0.1.0",
        description="Splits a contract into its clauses. An LLM locates the clauses; "
        "the clause text is always copied from the source document by code.",
    )
    app.state.settings = settings
    app.state.parser_factory = build_parser_factory(max_pages=settings.max_pages)

    app.add_middleware(UploadSizeLimitMiddleware, max_upload_bytes=settings.max_upload_bytes)
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
