"""The database enforces the rules the models declare."""

import secrets
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


async def test_values_of_a_failed_statement_stay_out_of_the_error(session: AsyncSession) -> None:
    """An error that nobody handled is logged with its message, and the values of an
    insert include the text of every clause."""
    clause = make_clause(0, text="CONFIDENTIAL CLAUSE TEXT", start_line=5, end_line=4)
    session.add(make_extraction(filename="confidential-nda.pdf", clauses=[clause]))

    with pytest.raises(IntegrityError) as raised:
        await session.commit()

    message = str(raised.value)
    # The statement is there to debug with. What was being stored is not.
    assert "INSERT INTO clauses" in message
    assert "CONFIDENTIAL" not in message
    assert "confidential-nda" not in message


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param({"position": -1}, id="negative position"),
        pytest.param({"start_line": 0, "end_line": 0}, id="line numbers start at one"),
        pytest.param({"start_line": 5, "end_line": 4}, id="line range reversed"),
        pytest.param({"start_page": 0, "end_page": 0}, id="page numbers start at one"),
        pytest.param({"start_page": 2, "end_page": 1}, id="page range reversed"),
        pytest.param({"start_page": 1, "end_page": None}, id="start page without end page"),
        pytest.param({"start_page": None, "end_page": 1}, id="end page without start page"),
    ],
)
async def test_check_constraints_reject_invalid_clauses(
    session: AsyncSession, invalid: dict[str, Any]
) -> None:
    clause = make_clause(**({"position": 0} | invalid))
    session.add(make_extraction(clauses=[clause]))

    with pytest.raises(IntegrityError):
        await session.commit()
