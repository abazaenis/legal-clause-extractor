"""Dependencies injected into the route handlers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings


def get_app_settings(request: Request) -> Settings:
    """The settings the application was created with."""
    return cast(Settings, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
