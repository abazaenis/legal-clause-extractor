"""Dependencies injected into the route handlers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings
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


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
ParserFactoryDependency = Annotated[ParserFactory, Depends(get_parser_factory)]
LLMProviderDependency = Annotated[LLMProvider, Depends(get_llm_provider)]
