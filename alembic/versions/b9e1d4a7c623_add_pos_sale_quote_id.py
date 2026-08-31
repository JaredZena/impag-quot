"""add pos_sale.quote_id (venta cierra cotización)

A POS ticket can optionally reference the quote it closes; completing the
sale marks an open (sent/viewed) quote accepted so the 'Cotizaciones
abiertas' pipeline drains without manual steps. Nullable — the link is
optional and existing tickets have none.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the new pos_sale column + FK + index are touched.

Revision ID: b9e1d4a7c623
Revises: f4a9c1d6b208
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9e1d4a7c623"
down_revision: str | Sequence[str] | None = "f4a9c1d6b208"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pos_sale", sa.Column("quote_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_pos_sale_quote_id",
        "pos_sale",
        "quote",
        ["quote_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_pos_sale_quote_id", "pos_sale", ["quote_id"])


def downgrade() -> None:
    op.drop_index("ix_pos_sale_quote_id", table_name="pos_sale")
    op.drop_constraint("fk_pos_sale_quote_id", "pos_sale", type_="foreignkey")
    op.drop_column("pos_sale", "quote_id")
