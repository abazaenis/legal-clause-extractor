"""Make the page fields optional.

Formats without pages, such as Word documents, have no page count and no page
range, so `extractions.page_count`, `clauses.start_page` and `clauses.end_page`
become nullable.

Revision ID: bbc187454381
Revises: 17c3c882bce3
Create Date: 2026-11-25 04:29:30.159676

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bbc187454381"
down_revision: str | Sequence[str] | None = "17c3c882bce3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAGE_RANGE_CHECK = "ck_clauses_page_range_valid"


def upgrade() -> None:
    """Allow rows without pages, and keep a page range valid whenever one is given."""
    with op.batch_alter_table("clauses") as batch_op:
        batch_op.alter_column("start_page", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("end_page", existing_type=sa.Integer(), nullable=True)
        # A check constraint only fails when its result is false, and a comparison
        # with NULL is unknown. The old rule would therefore accept a start page
        # without an end page. The IS NOT NULL tests make that case false.
        batch_op.drop_constraint(batch_op.f(PAGE_RANGE_CHECK), type_="check")
        batch_op.create_check_constraint(
            batch_op.f(PAGE_RANGE_CHECK),
            "(start_page IS NULL AND end_page IS NULL) "
            "OR (start_page IS NOT NULL AND end_page IS NOT NULL "
            "AND start_page >= 1 AND end_page >= start_page)",
        )

    with op.batch_alter_table("extractions") as batch_op:
        batch_op.alter_column("page_count", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Require pages again.

    This fails if any row has no pages. That is intended: such a row cannot be
    represented in the older schema, and guessing a page for it would be wrong.
    """
    with op.batch_alter_table("extractions") as batch_op:
        batch_op.alter_column("page_count", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("clauses") as batch_op:
        batch_op.drop_constraint(batch_op.f(PAGE_RANGE_CHECK), type_="check")
        batch_op.create_check_constraint(
            batch_op.f(PAGE_RANGE_CHECK), "start_page >= 1 AND end_page >= start_page"
        )
        batch_op.alter_column("end_page", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("start_page", existing_type=sa.Integer(), nullable=False)
