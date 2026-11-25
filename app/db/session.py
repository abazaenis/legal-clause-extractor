"""Async engine and session factory."""

from sqlalchemy import event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry


def create_engine(database_url: str) -> AsyncEngine:
    """Create the async engine, applying SQLite settings when the URL points at SQLite.

    `hide_parameters` keeps the values of a statement out of error messages and logs.
    The values include the text of every clause, and an error that nobody handled is
    logged with its message.
    """
    engine = create_async_engine(database_url, hide_parameters=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions keep their loaded objects usable after commit, which async code needs."""
    return async_sessionmaker(engine, expire_on_commit=False)


def _configure_sqlite(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """Apply per-connection SQLite settings.

    SQLite ships with foreign keys switched off, so without this the cascades and
    foreign-key checks declared on the models would silently do nothing.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Write-ahead logging lets readers keep reading while a write is in progress.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
