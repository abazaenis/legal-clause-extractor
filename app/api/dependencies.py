"""Dependencies injected into the route handlers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings
from app.extraction.parsers.factory import ParserFactory


def get_app_settings(request: Request) -> Settings:
    """The settings the application was created with."""
    return cast(Settings, request.app.state.settings)


def get_parser_factory(request: Request) -> ParserFactory:
    """The document parsers the application was created with."""
    return cast(ParserFactory, request.app.state.parser_factory)


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
ParserFactoryDependency = Annotated[ParserFactory, Depends(get_parser_factory)]
