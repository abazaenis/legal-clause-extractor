"""Storing an extraction and finding it again."""

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


async def test_storing_the_same_file_again_replaces_its_result_and_keeps_the_id(
    extractions: ExtractionRepository, session: AsyncSession
) -> None:
    first = await extractions.save(SOURCE, result(clause(0), clause(1), clause(2)))
    renamed = replace(SOURCE, filename="nda-final.pdf")

    second = await extractions.save(
        renamed, result(clause(0, type=ClauseType.PREAMBLE), metadata=ContractMetadata())
    )

    assert not second.created
    assert second.extraction.id == first.extraction.id
    assert second.extraction.created_at >= first.extraction.created_at
    found = await extractions.find_by_sha256(SOURCE.sha256)
    assert found is not None
    # The row is replaced as a whole: nothing of the first result is left.
    assert (found.filename, found.title, found.parties) == ("nda-final.pdf", None, [])
    assert [(c.position, c.type) for c in found.clauses] == [(0, "preamble")]
    assert await count(session, Extraction) == 1
    assert await count(session, Clause) == 1


async def test_result_is_stored_completely_or_not_at_all(
    extractions: ExtractionRepository, session: AsyncSession
) -> None:
    # The second clause breaks a rule of the database: a line range that runs backwards.
    broken = result(clause(0), clause(1, start_line=9, end_line=3))

    with pytest.raises(IntegrityError):
        await extractions.save(SOURCE, broken)

    assert await count(session, Extraction) == 0
    assert await count(session, Clause) == 0


async def test_failed_replacement_leaves_the_stored_result_as_it_was(
    extractions: ExtractionRepository, session: AsyncSession
) -> None:
    await extractions.save(SOURCE, result(clause(0), clause(1)))
    broken = result(clause(0, start_line=9, end_line=3), metadata=ContractMetadata())

    with pytest.raises(IntegrityError):
        await extractions.save(SOURCE, broken)

    found = await extractions.find_by_sha256(SOURCE.sha256)
    assert found is not None
    assert found.title == "MUTUAL NON-DISCLOSURE AGREEMENT"
    assert [c.number for c in found.clauses] == ["1", "2"]
    assert await count(session, Clause) == 2


class LateRepository(ExtractionRepository):
    """Looks for the file a moment too early: another request is just about to store it."""

    looked = False

    async def _find(self, session: AsyncSession, sha256: str) -> Extraction | None:
        if not self.looked:
            self.looked = True
            return None
        return await super()._find(session, sha256)


async def test_two_requests_storing_the_same_new_file_leave_one_row(
    engine: AsyncEngine, extractions: ExtractionRepository, session: AsyncSession
) -> None:
    winner = await extractions.save(SOURCE, result(clause(0)))
    late = LateRepository(create_session_factory(engine))

    # This insert collides with the unique hash. The repository tries once more.
    loser = await late.save(SOURCE, result(clause(0), clause(1)))

    assert not loser.created
    assert loser.extraction.id == winner.extraction.id
    assert await count(session, Extraction) == 1
    assert await count(session, Clause) == 2


async def test_no_connection_is_held_between_calls(
    engine: AsyncEngine, extractions: ExtractionRepository
) -> None:
    """The LLM call sits between the lookup and the save, and can take minutes."""
    pool = engine.sync_engine.pool

    await extractions.find_by_sha256(SOURCE.sha256)
    assert pool.checkedout() == 0  # type: ignore[attr-defined]

    await extractions.save(SOURCE, result())
    assert pool.checkedout() == 0  # type: ignore[attr-defined]
