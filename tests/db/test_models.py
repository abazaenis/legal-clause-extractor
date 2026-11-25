"""The database enforces the rules the models declare."""

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Clause, Extraction


def make_extraction(**overrides: Any) -> Extraction:
    values: dict[str, Any] = {
        "filename": "nda.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "page_count": 3,
        # A different file per call, because a file can have only one extraction.
        "sha256": secrets.token_hex(32),
        "llm_provider": "fake",
        "llm_model": "fake-model",
        "prompt_version": "v1",
        "input_tokens": 100,
        "output_tokens": 20,
        "llm_duration_ms": 5,
    }
    return Extraction(**(values | overrides))


def make_clause(position: int, **overrides: Any) -> Clause:
    values: dict[str, Any] = {
        "position": position,
        "type": "other",
        "text": f"Text of clause {position}.",
        "start_line": position + 1,
        "end_line": position + 1,
        "start_page": 1,
        "end_page": 1,
    }
    return Clause(**(values | overrides))


async def count_clauses(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Clause)) or 0


async def test_extraction_round_trips_with_its_clauses(session: AsyncSession) -> None:
    definitions = make_clause(0, type="definitions", number="1", title="Definitions")
    sub_clause = make_clause(1, number="1.1")
    remedies = make_clause(2, type="other", number="2", title="Remedies")
    extraction = make_extraction(
        title="MUTUAL NON-DISCLOSURE AGREEMENT",
        contract_type="nda",
        effective_date=date(2023, 8, 11),
        parties=[{"name": "Leumi UK Group Limited", "role": "Funder"}],
        warnings=[{"code": "title_not_verified", "message": "Title not found in the source."}],
        page_noise=[{"text": "Page # of #", "occurrences": 2}],
        # Deliberately out of order: the relationship must return them by position.
        clauses=[remedies, sub_clause, definitions],
    )
    session.add(extraction)
    await session.commit()
    session.expunge_all()

    loaded = await session.scalar(select(Extraction).options(selectinload(Extraction.clauses)))

    assert loaded is not None
    assert loaded.id.version == 7
    assert loaded.title == "MUTUAL NON-DISCLOSURE AGREEMENT"
    assert loaded.effective_date == date(2023, 8, 11)
    assert loaded.parties == [{"name": "Leumi UK Group Limited", "role": "Funder"}]
    assert loaded.warnings == [
        {"code": "title_not_verified", "message": "Title not found in the source."}
    ]
    assert loaded.page_noise == [{"text": "Page # of #", "occurrences": 2}]
    assert [clause.position for clause in loaded.clauses] == [0, 1, 2]
    assert [clause.number for clause in loaded.clauses] == ["1", "1.1", "2"]


async def test_json_columns_default_to_empty_lists(session: AsyncSession) -> None:
    extraction = make_extraction()
    session.add(extraction)
    await session.commit()

    assert extraction.parties == []
    assert extraction.warnings == []
    assert extraction.page_noise == []


async def test_ids_sort_by_creation_order(session: AsyncSession) -> None:
    first, second = make_extraction(), make_extraction()
    session.add(first)
    await session.flush()
    session.add(second)
    await session.flush()

    assert first.id < second.id


async def test_created_at_is_stored_and_returned_in_utc(session: AsyncSession) -> None:
    two_hours_ahead = timezone(timedelta(hours=2))
    extraction = make_extraction(created_at=datetime(2026, 1, 1, 12, 0, tzinfo=two_hours_ahead))
    session.add(extraction)
    await session.commit()
    session.expunge_all()

    loaded = await session.scalar(select(Extraction))

    assert loaded is not None
    assert loaded.created_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert loaded.created_at.tzinfo is UTC


async def test_created_at_defaults_to_now(session: AsyncSession) -> None:
    before = datetime.now(UTC)
    extraction = make_extraction()
    session.add(extraction)
    await session.commit()

    assert before <= extraction.created_at <= datetime.now(UTC)


async def test_naive_datetime_is_rejected(session: AsyncSession) -> None:
    session.add(make_extraction(created_at=datetime(2026, 1, 1, 12, 0)))

    with pytest.raises(StatementError, match="Naive datetimes are not accepted"):
        await session.commit()


async def test_a_file_can_have_only_one_extraction(session: AsyncSession) -> None:
    same_file = "a" * 64
    session.add(make_extraction(sha256=same_file))
    await session.commit()

    session.add(make_extraction(sha256=same_file))

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_position_is_unique_within_an_extraction(session: AsyncSession) -> None:
    session.add(make_extraction(clauses=[make_clause(0), make_clause(0)]))

    with pytest.raises(IntegrityError):
        await session.commit()


async def test_same_position_is_allowed_in_different_extractions(session: AsyncSession) -> None:
    session.add_all(
        [make_extraction(clauses=[make_clause(0)]), make_extraction(clauses=[make_clause(0)])]
    )
    await session.commit()

    assert await count_clauses(session) == 2


async def test_database_cascade_deletes_clauses_with_their_extraction(
    session: AsyncSession,
) -> None:
    extraction = make_extraction(clauses=[make_clause(0), make_clause(1)])
    session.add(extraction)
    await session.commit()
    assert await count_clauses(session) == 2

    # A Core DELETE bypasses the ORM, so only the database's own ON DELETE CASCADE
    # can remove the clauses. This fails if SQLite foreign keys are not switched on.
    await session.execute(delete(Extraction).where(Extraction.id == extraction.id))
    await session.commit()

    assert await count_clauses(session) == 0


async def test_clause_must_belong_to_an_existing_extraction(session: AsyncSession) -> None:
    session.add(make_clause(0, extraction_id=uuid.uuid7()))

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param({"position": -1}, id="negative position"),
        pytest.param({"start_line": 0, "end_line": 0}, id="line numbers start at one"),
        pytest.param({"start_line": 5, "end_line": 4}, id="line range reversed"),
        pytest.param({"start_page": 0, "end_page": 0}, id="page numbers start at one"),
        pytest.param({"start_page": 2, "end_page": 1}, id="page range reversed"),
    ],
)
async def test_check_constraints_reject_invalid_clauses(
    session: AsyncSession, invalid: dict[str, Any]
) -> None:
    clause = make_clause(**({"position": 0} | invalid))
    session.add(make_extraction(clauses=[clause]))

    with pytest.raises(IntegrityError):
        await session.commit()
