"""add WhatsApp *Venta* columns to sale

From 2026-10-01 the team's *Venta NN_MM_YYYY* WhatsApp message, pasted in
the admin, is the sales ledger (sheet_tab='WHATSAPP'); the VENTAS sheet rows
after that date are quarantined by services/sales_sync.py. The message
carries what the sheet never had: the payments (anticipos), what is still
owed, and the quote the sale closes.

All columns nullable; sheet and POS rows leave them NULL.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: c8e2f4a1b9d7
Revises: b3f8d1e6a4c2
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8e2f4a1b9d7"
down_revision: str | Sequence[str] | None = "b3f8d1e6a4c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sale", sa.Column("paid_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("sale", sa.Column("pending_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("sale", sa.Column("payments", sa.JSON(), nullable=True))
    op.add_column(
        "sale",
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("quote.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_sale_quote_id", "sale", ["quote_id"])
    op.add_column("sale", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sale", "notes")
    op.drop_index("ix_sale_quote_id", table_name="sale")
    op.drop_column("sale", "quote_id")
    op.drop_column("sale", "payments")
    op.drop_column("sale", "pending_amount")
    op.drop_column("sale", "paid_amount")
