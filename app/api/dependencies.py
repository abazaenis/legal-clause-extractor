"""Dependencies injected into the route handlers."""

from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.repository import ExtractionRepository
from app.extraction.parsers.factory import ParserFactory
from app.llm.base import LLMProvider


def get_app_settings(request: Request) -> Settings:
    """The settings the application was created with."""
    return cast(Settings, request.app.state.settings)


def get_parser_factory(request: Request) -> ParserFactory:
    """The document parsers the application was created with."""
    return cast(ParserFactory, request.app.state.parser_factory)


def get_llm_provider(request: Request) -> LLMProvider:
    """The LLM provider built when the server started.

    Tests replace this dependency with a fake provider, so they never need a key
    or a network.
    """
    return cast(LLMProvider, request.app.state.llm_provider)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The database session factory built when the server started.

    Tests replace this dependency with a factory for a temporary database.
    """
    return cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)


def get_extraction_repository(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> ExtractionRepository:
    return ExtractionRepository(session_factory)


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
ParserFactoryDependency = Annotated[ParserFactory, Depends(get_parser_factory)]
LLMProviderDependency = Annotated[LLMProvider, Depends(get_llm_provider)]
ExtractionRepositoryDependency = Annotated[ExtractionRepository, Depends(get_extraction_repository)]
