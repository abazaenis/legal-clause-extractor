"""The migrations and the models describe the same schema."""

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


def test_downgrade_removes_every_table(database_file: Path, alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    assert {"extractions", "clauses"} <= table_names(database_file)

    command.downgrade(alembic_config, "base")

    assert table_names(database_file) == {"alembic_version"}
