"""Storing an extraction and finding it again."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.models import Clause, Extraction
from app.db.repository import ExtractionRepository
from app.db.session import create_session_factory
from app.extraction.file_types import FileType
from app.extraction.labels import ClauseType, ContractType
from app.extraction.models import (
    ContractMetadata,
    ExtractedClause,
    ExtractionResult,
    ExtractionWarning,
    LLMUsage,
    PageNoise,
    Party,
    SourceFile,
)

SOURCE = SourceFile(filename="nda.pdf", file_type=FileType.PDF, data=b"%PDF-1.7", sha256="a" * 64)


def clause(position: int, **overrides: object) -> ExtractedClause:
    base = ExtractedClause(
        position=position,
        type=ClauseType.OTHER,
        number=str(position + 1),
        title=None,
        text=f"{position + 1}. Text of the clause.",
        start_line=position + 1,
        end_line=position + 1,
        start_page=1,
        end_page=1,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def result(*clauses: ExtractedClause, **overrides: object) -> ExtractionResult:
    base = ExtractionResult(
        page_count=3,
        metadata=ContractMetadata(
            title="MUTUAL NON-DISCLOSURE AGREEMENT",
            contract_type=ContractType.NDA,
            effective_date=date(2023, 8, 11),
            parties=(Party(name="Leumi UK Group Limited", role="Funder"), Party(role="Customer")),
        ),
        clauses=clauses or (clause(0), clause(1)),
        warnings=(ExtractionWarning(code="hidden_text_removed", message="Left out a line."),),
        page_noise=(PageNoise(text="Page # of #", occurrences=2),),
        llm=LLMUsage(
            provider="fake",
            model="fake-model",
            prompt_version="v2",
            input_tokens=1000,
            output_tokens=50,
            duration_ms=7,
        ),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def extractions(engine: AsyncEngine) -> ExtractionRepository:
    return ExtractionRepository(create_session_factory(engine))


async def count(session: AsyncSession, table: type[Extraction] | type[Clause]) -> int:
    return await session.scalar(select(func.count()).select_from(table)) or 0


async def test_stored_result_is_found_by_the_hash_of_its_file(
    extractions: ExtractionRepository,
) -> None:
    before = datetime.now(UTC)
    stored = await extractions.save(SOURCE, result(clause(0, title="Definitions"), clause(1)))

    found = await extractions.find_by_sha256(SOURCE.sha256)

    assert stored.created
    assert found is not None
    assert found.id == stored.extraction.id
    assert found.id.version == 7
    assert before <= found.created_at <= datetime.now(UTC)
    # The file, as it was uploaded.
    assert (found.filename, found.content_type, found.size_bytes) == (
        "nda.pdf",
        "application/pdf",
        len(SOURCE.data),
    )
    assert (found.page_count, found.sha256) == (3, SOURCE.sha256)
    # The contract metadata.
    assert (found.title, found.contract_type) == ("MUTUAL NON-DISCLOSURE AGREEMENT", "nda")
    assert found.effective_date == date(2023, 8, 11)
    assert found.parties == [
        {"name": "Leumi UK Group Limited", "role": "Funder"},
        {"name": None, "role": "Customer"},
    ]
    # The LLM run.
    assert (found.llm_provider, found.llm_model, found.prompt_version) == (
        "fake",
        "fake-model",
        "v2",
    )
    assert (found.input_tokens, found.output_tokens, found.llm_duration_ms) == (1000, 50, 7)
    assert found.warnings == [{"code": "hidden_text_removed", "message": "Left out a line."}]
    assert found.page_noise == [{"text": "Page # of #", "occurrences": 2}]
    # The clauses, in document order.
    assert [
        (c.position, c.type, c.number, c.title, c.text, c.start_line, c.end_line, c.start_page)
        for c in found.clauses
    ] == [
        (0, "other", "1", "Definitions", "1. Text of the clause.", 1, 1, 1),
        (1, "other", "2", None, "2. Text of the clause.", 2, 2, 1),
    ]


async def test_file_that_was_never_stored_is_not_found(extractions: ExtractionRepository) -> None:
    await extractions.save(SOURCE, result())

    assert await extractions.find_by_sha256("b" * 64) is None


async def test_document_without_pages_or_metadata_can_be_stored(
    extractions: ExtractionRepository,
) -> None:
    no_pages = clause(0, start_page=None, end_page=None)

    await extractions.save(
        SOURCE, result(no_pages, page_count=None, metadata=ContractMetadata(), page_noise=())
    )

    found = await extractions.find_by_sha256(SOURCE.sha256)
    assert found is not None
    assert (found.page_count, found.title, found.contract_type) == (None, None, None)
    assert (found.effective_date, found.parties, found.page_noise) == (None, [], [])
    assert (found.clauses[0].start_page, found.clauses[0].end_page) == (None, None)


async def test_storing_the_same_file_again_keeps_the_stored_result(
    extractions: ExtractionRepository, session: AsyncSession
) -> None:
    """Two requests for the same new file can both run the pipeline and both save. The
    first one to write wins. The other one finds the hash taken and gets that result."""
    first = await extractions.save(SOURCE, result(clause(0), clause(1), clause(2)))
    renamed = replace(SOURCE, filename="nda-final.pdf")

    second = await extractions.save(
        renamed, result(clause(0, type=ClauseType.PREAMBLE), metadata=ContractMetadata())
    )

    assert not second.created
    assert second.extraction.id == first.extraction.id
    assert second.extraction.created_at == first.extraction.created_at
    # The stored result is the first one, untouched.
    assert (second.extraction.filename, second.extraction.title) == (
        "nda.pdf",
        "MUTUAL NON-DISCLOSURE AGREEMENT",
    )
    assert [c.number for c in second.extraction.clauses] == ["1", "2", "3"]
    assert await count(session, Extraction) == 1
    assert await count(session, Clause) == 3


async def test_result_is_stored_completely_or_not_at_all(
    extractions: ExtractionRepository, session: AsyncSession
) -> None:
    # The second clause breaks a rule of the database: a line range that runs backwards.
    broken = result(clause(0), clause(1, start_line=9, end_line=3))

    with pytest.raises(IntegrityError):
        await extractions.save(SOURCE, broken)

    assert await count(session, Extraction) == 0
    assert await count(session, Clause) == 0


async def test_no_connection_is_held_between_calls(
    engine: AsyncEngine, extractions: ExtractionRepository
) -> None:
    """The LLM call sits between the lookup and the save, and can take minutes."""
    pool = engine.sync_engine.pool

    await extractions.find_by_sha256(SOURCE.sha256)
    assert pool.checkedout() == 0  # type: ignore[attr-defined]

    stored = await extractions.save(SOURCE, result())
    assert pool.checkedout() == 0  # type: ignore[attr-defined]

    await extractions.find_by_id(stored.extraction.id)
    await extractions.list_page(limit=10, offset=0)
    assert pool.checkedout() == 0  # type: ignore[attr-defined]


# --- Reading: one extraction by its id, and a page of them --------------------------------------


def file_number(number: int) -> SourceFile:
    """A file of its own: another name and another hash."""
    return replace(SOURCE, filename=f"contract-{number}.pdf", sha256=f"{number:064d}")


async def test_stored_result_is_found_by_its_id(extractions: ExtractionRepository) -> None:
    stored = await extractions.save(SOURCE, result(clause(0), clause(1)))
    await extractions.save(file_number(2), result())

    found = await extractions.find_by_id(stored.extraction.id)

    assert found is not None
    assert (found.id, found.sha256) == (stored.extraction.id, SOURCE.sha256)
    # It comes back complete, with its clauses.
    assert [c.number for c in found.clauses] == ["1", "2"]


async def test_id_that_was_never_given_out_is_not_found(extractions: ExtractionRepository) -> None:
    await extractions.save(SOURCE, result())

    assert await extractions.find_by_id(uuid.uuid7()) is None


async def test_page_lists_the_newest_first_with_the_number_of_clauses(
    extractions: ExtractionRepository,
) -> None:
    for number in (1, 2, 3):
        await extractions.save(
            file_number(number), result(*(clause(position) for position in range(number)))
        )

    page = await extractions.list_page(limit=10, offset=0)

    assert page.total == 3
    assert [(listed.extraction.filename, listed.clause_count) for listed in page.items] == [
        ("contract-3.pdf", 3),
        ("contract-2.pdf", 2),
        ("contract-1.pdf", 1),
    ]
    # Everything a summary needs is on the row.
    assert page.items[0].extraction.title == "MUTUAL NON-DISCLOSURE AGREEMENT"
    assert page.items[0].extraction.warnings == [
        {"code": "hidden_text_removed", "message": "Left out a line."}
    ]


async def test_page_is_cut_by_limit_and_offset(extractions: ExtractionRepository) -> None:
    for number in range(1, 6):
        await extractions.save(file_number(number), result())

    async def names(limit: int, offset: int) -> list[str]:
        page = await extractions.list_page(limit=limit, offset=offset)
        assert page.total == 5
        return [listed.extraction.filename for listed in page.items]

    assert await names(2, 0) == ["contract-5.pdf", "contract-4.pdf"]
    assert await names(2, 2) == ["contract-3.pdf", "contract-2.pdf"]
    assert await names(2, 4) == ["contract-1.pdf"]
    assert await names(2, 6) == []


async def test_page_of_nothing_is_empty(extractions: ExtractionRepository) -> None:
    page = await extractions.list_page(limit=10, offset=0)

    assert (page.items, page.total) == ((), 0)
