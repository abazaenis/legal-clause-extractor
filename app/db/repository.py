"""Reads and writes stored extractions."""

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Clause, Extraction
from app.extraction.models import ExtractionResult, SourceFile


@dataclass(frozen=True, slots=True)
class StoredExtraction:
    """A stored extraction, and whether this request was the one that created it."""

    extraction: Extraction
    # False when the file already had a stored result, whether it was returned as it
    # is or replaced by a new one.
    created: bool


@dataclass(frozen=True, slots=True)
class ListedExtraction:
    """A stored extraction as a list shows it: without its clauses, with their number."""

    # Its clauses are not loaded, and must not be read from this row.
    extraction: Extraction
    clause_count: int


@dataclass(frozen=True, slots=True)
class ExtractionPage:
    """One page of the stored extractions, newest first."""

    items: tuple[ListedExtraction, ...]
    # How many extractions are stored in all, on every page together.
    total: int


class ExtractionRepository:
    """The stored extractions, one per file.

    Every method opens a session of its own and closes it before it returns, so no
    database connection is held while the LLM works on a request. A single extraction
    comes back complete, with its clauses. A page of them comes back without.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def find_by_sha256(self, sha256: str) -> Extraction | None:
        """Return the stored extraction of the file with this hash, if there is one."""
        async with self._session_factory() as session:
            return await self._find(session, sha256)

    async def find_by_id(self, extraction_id: uuid.UUID) -> Extraction | None:
        """Return the stored extraction with this id, if there is one."""
        async with self._session_factory() as session:
            extraction: Extraction | None = await session.scalar(
                select(Extraction)
                .where(Extraction.id == extraction_id)
                .options(selectinload(Extraction.clauses))
            )
            return extraction

    async def list_page(self, *, limit: int, offset: int) -> ExtractionPage:
        """Return `limit` stored extractions after skipping `offset`, newest first.

        The clauses are not read: a list shows summaries, and the clauses hold the
        whole text of every contract. Their number is counted in the database.
        """
        clause_count = (
            select(func.count())
            .select_from(Clause)
            .where(Clause.extraction_id == Extraction.id)
            .correlate(Extraction)
            .scalar_subquery()
        )
        page = (
            select(Extraction, clause_count)
            # The id breaks a tie between two results of the same moment, so the
            # order is the same on every request and no row is on two pages.
            .order_by(Extraction.created_at.desc(), Extraction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(page)).all()
            total = await session.scalar(select(func.count()).select_from(Extraction)) or 0
        return ExtractionPage(
            items=tuple(
                ListedExtraction(extraction=extraction, clause_count=count)
                for extraction, count in rows
            ),
            total=total,
        )

    async def save(self, source: SourceFile, result: ExtractionResult) -> StoredExtraction:
        """Store the result for a file, in one transaction.

        A file has one stored result. If the file already has one, the new result
        takes its place and the id stays the same.
        """
        try:
            return await self._save(source, result)
        except IntegrityError:
            # Two requests for the same new file can both find nothing stored and both
            # insert. The unique hash lets one of them through. The other one ends up
            # here, finds the row on its second attempt and replaces the result.
            return await self._save(source, result)

    async def _save(self, source: SourceFile, result: ExtractionResult) -> StoredExtraction:
        async with self._session_factory() as session, session.begin():
            row = await self._find(session, source.sha256)
            created = row is None
            if row is None:
                row = Extraction(sha256=source.sha256)
                session.add(row)
            else:
                # The old clauses must be gone before the new ones take their positions.
                row.clauses.clear()
                await session.flush()
            _fill(row, source, result)
        return StoredExtraction(extraction=row, created=created)

    async def _find(self, session: AsyncSession, sha256: str) -> Extraction | None:
        extraction: Extraction | None = await session.scalar(
            select(Extraction)
            .where(Extraction.sha256 == sha256)
            .options(selectinload(Extraction.clauses))
        )
        return extraction


def _fill(row: Extraction, source: SourceFile, result: ExtractionResult) -> None:
    """Write a result into a row. The row is replaced as a whole, never in part."""
    row.created_at = datetime.now(UTC)

    row.filename = source.filename
    row.content_type = source.file_type.media_type
    row.size_bytes = source.size_bytes
    row.page_count = result.page_count

    metadata = result.metadata
    row.title = metadata.title
    row.contract_type = metadata.contract_type.value if metadata.contract_type else None
    row.effective_date = metadata.effective_date
    row.parties = [asdict(party) for party in metadata.parties]

    row.llm_provider = result.llm.provider
    row.llm_model = result.llm.model
    row.prompt_version = result.llm.prompt_version
    row.input_tokens = result.llm.input_tokens
    row.output_tokens = result.llm.output_tokens
    row.llm_duration_ms = result.llm.duration_ms

    row.warnings = [asdict(warning) for warning in result.warnings]
    row.page_noise = [asdict(noise) for noise in result.page_noise]

    row.clauses.extend(
        Clause(
            position=clause.position,
            type=clause.type.value,
            number=clause.number,
            title=clause.title,
            text=clause.text,
            start_line=clause.start_line,
            end_line=clause.end_line,
            start_page=clause.start_page,
            end_page=clause.end_page,
        )
        for clause in result.clauses
    )
