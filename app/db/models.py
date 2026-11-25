"""ORM models: one row per extracted document, one row per clause."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Extraction(Base):
    """One file and everything extracted from it.

    A file has exactly one extraction. Processing the same file again replaces the row
    as a whole and keeps its id; rows are never partially updated.
    """

    __tablename__ = "extractions"

    # UUIDv7 is time-ordered, so ids sort by creation time and index well.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utc_now)

    # File facts. The file itself is not stored. Its SHA-256 identifies it: the hash is
    # unique, so the same file always maps to the same row and the same id.
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    page_count: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64), unique=True)

    # Contract metadata read from the document. Null means not found, or not verifiable
    # against the source text.
    title: Mapped[str | None] = mapped_column(String(500))
    contract_type: Mapped[str | None] = mapped_column(String(50))
    effective_date: Mapped[date | None]
    parties: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    # Facts about the LLM run that produced this extraction.
    llm_provider: Mapped[str] = mapped_column(String(50))
    llm_model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    llm_duration_ms: Mapped[int]

    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Page headers, footers and page numbers that were left out of the clause text.
    page_noise: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    clauses: Mapped[list[Clause]] = relationship(
        back_populates="extraction",
        order_by="Clause.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Clause(Base):
    """One clause of a document. The text is copied verbatim from the source.

    Clauses are stored as one flat list in document order. There is no parent-child
    structure; where a contract is hierarchical, the clause `number` shows it.
    """

    __tablename__ = "clauses"
    __table_args__ = (
        UniqueConstraint("extraction_id", "position", name="uq_clauses_extraction_id_position"),
        CheckConstraint("position >= 0", name="position_not_negative"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="line_range_valid"),
        CheckConstraint("start_page >= 1 AND end_page >= start_page", name="page_range_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractions.id", ondelete="CASCADE")
    )
    # Zero-based order of the clause within its document.
    position: Mapped[int]

    type: Mapped[str] = mapped_column(String(50))
    number: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)

    # Where the clause sits in the source document (one-based, inclusive).
    start_line: Mapped[int]
    end_line: Mapped[int]
    start_page: Mapped[int]
    end_page: Mapped[int]

    extraction: Mapped[Extraction] = relationship(back_populates="clauses")
