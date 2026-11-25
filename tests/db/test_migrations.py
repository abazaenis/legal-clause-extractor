"""The migrations and the models describe the same schema."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.config import get_settings
from app.db import models  # noqa: F401  (importing registers the tables on Base.metadata)
from app.db.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the application settings, which the migrations read, at a temporary database."""
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    yield database
    get_settings.cache_clear()


@pytest.fixture
def alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def table_names(database: Path) -> set[str]:
    engine = sqlalchemy.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        return set(sqlalchemy.inspect(engine).get_table_names())
    finally:
        engine.dispose()


# These tests are synchronous on purpose: Alembic's env.py starts its own event loop.


def test_upgrade_builds_the_schema_the_models_describe(
    database_file: Path, alembic_config: Config
) -> None:
    command.upgrade(alembic_config, "head")

    engine = sqlalchemy.create_engine(f"sqlite:///{database_file.as_posix()}")
    try:
        with engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()

    assert differences == []


FIRST_REVISION = "17c3c882bce3"


def insert_extraction_with_two_clauses(database: Path) -> None:
    """Store rows the way the first revision's schema expects them."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO extractions (id, created_at, filename, content_type, size_bytes,"
            " page_count, sha256, parties, llm_provider, llm_model, prompt_version,"
            " input_tokens, output_tokens, llm_duration_ms, warnings, page_noise)"
            " VALUES ('e1', '2026-01-01 00:00:00', 'nda.pdf', 'application/pdf', 1, 3, 'hash',"
            " '[]', 'fake', 'fake-model', 'v1', 1, 1, 1, '[]', '[]')"
        )
        connection.executemany(
            "INSERT INTO clauses (id, extraction_id, position, type, text, start_line,"
            " end_line, start_page, end_page) VALUES (?, 'e1', ?, 'other', 'Text.', 1, 1, 1, 1)",
            [("c0", 0), ("c1", 1)],
        )
        connection.commit()
    finally:
        connection.close()


def row_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database)
    try:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("extractions", "clauses")
        }
    finally:
        connection.close()


def test_migrations_keep_the_rows_that_are_already_stored(
    database_file: Path, alembic_config: Config
) -> None:
    """SQLite changes a table by rebuilding it, which must not lose data.

    With foreign keys enforced during a migration, dropping the old `extractions`
    table cascades into `clauses` and silently deletes every clause.
    """
    command.upgrade(alembic_config, FIRST_REVISION)
    insert_extraction_with_two_clauses(database_file)

    command.upgrade(alembic_config, "head")
    assert row_counts(database_file) == {"extractions": 1, "clauses": 2}

    command.downgrade(alembic_config, FIRST_REVISION)
    assert row_counts(database_file) == {"extractions": 1, "clauses": 2}


def test_migrated_schema_accepts_rows_without_pages(
    database_file: Path, alembic_config: Config
) -> None:
    command.upgrade(alembic_config, FIRST_REVISION)
    insert_extraction_with_two_clauses(database_file)
    command.upgrade(alembic_config, "head")

    connection = sqlite3.connect(database_file)
    try:
        # No pages at all is fine.
        connection.execute("UPDATE extractions SET page_count = NULL")
        connection.execute("UPDATE clauses SET start_page = NULL, end_page = NULL WHERE id = 'c0'")
        connection.commit()

        # Half a page range is not.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE clauses SET start_page = NULL WHERE id = 'c1'")
    finally:
        connection.close()


def test_downgrade_removes_every_table(database_file: Path, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    assert {"extractions", "clauses"} <= table_names(database_file)

    command.downgrade(alembic_config, "base")

    assert table_names(database_file) == {"alembic_version"}
