"""Alembic environment: runs migrations against the database the application uses."""

import asyncio
from logging.config import fileConfig
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db import models  # noqa: F401  (importing registers the tables on Base.metadata)
from app.db.base import Base
from app.db.session import create_engine
from app.db.types import UTCDateTime

config = context.config

if config.config_file_name is not None:
    # Keep loggers that already exist, so running migrations from inside the
    # application or the tests does not silence their logging.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render application column types as plain SQLAlchemy types.

    A migration is a frozen record of a schema change, so it must not import
    application code that can change after the migration was written.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    """Emit the migration SQL without connecting to a database."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    # Batch mode lets Alembic rebuild a table when SQLite cannot alter it in place.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run the migrations through the same engine factory the application uses."""
    engine = create_engine(get_settings().database_url)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
