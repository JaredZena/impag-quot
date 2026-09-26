"""add tax_declaration (impuestos declarados por periodo)

Monthly SAT declaration totals behind the Punto de equilibrio page. Taxes
scale with sales (~6.5% in 2026), so the break-even subtracts a measured tax
rate from the gross margin instead of carrying taxes as a fixed cost.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new table is created.

Revision ID: f1c4a9e2d756
Revises: d5e2b8f4a613
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1c4a9e2d756"
down_revision: str | Sequence[str] | None = "d5e2b8f4a613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tax_declaration",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("month", name="uq_tax_declaration_month"),
    )
    op.create_index("ix_tax_declaration_id", "tax_declaration", ["id"])


def downgrade() -> None:
    op.drop_index("ix_tax_declaration_id", table_name="tax_declaration")
    op.drop_table("tax_declaration")
