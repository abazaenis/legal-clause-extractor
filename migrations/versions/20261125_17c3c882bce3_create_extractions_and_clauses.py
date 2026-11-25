"""Create the extractions and clauses tables.

Revision ID: 17c3c882bce3
Revises:
Create Date: 2026-11-25 02:30:39.828184

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "17c3c882bce3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create both tables."""
    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("contract_type", sa.String(length=50), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("parties", sa.JSON(), nullable=False),
        sa.Column("llm_provider", sa.String(length=50), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("llm_duration_ms", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("page_noise", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extractions")),
        sa.UniqueConstraint("sha256", name=op.f("uq_extractions_sha256")),
    )
    op.create_table(
        "clauses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("number", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("start_page", sa.Integer(), nullable=False),
        sa.Column("end_page", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name=op.f("ck_clauses_position_not_negative")),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line", name=op.f("ck_clauses_line_range_valid")
        ),
        sa.CheckConstraint(
            "start_page >= 1 AND end_page >= start_page", name=op.f("ck_clauses_page_range_valid")
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["extractions.id"],
            name=op.f("fk_clauses_extraction_id_extractions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clauses")),
        sa.UniqueConstraint("extraction_id", "position", name="uq_clauses_extraction_id_position"),
    )


def downgrade() -> None:
    """Drop both tables, clauses first because it references extractions."""
    op.drop_table("clauses")
    op.drop_table("extractions")
