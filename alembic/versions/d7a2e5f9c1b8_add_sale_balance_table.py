"""add sale_balance table (per-sale cost/margin from BALANCES DE VENTA sheet)

One row per spreadsheet tab, keyed by tab_title so re-syncs upsert in place.
Margin facts come from joining the tab's folios against the sale ledger.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new table + its indexes are touched.

Revision ID: d7a2e5f9c1b8
Revises: b4d8f1a6c3e7
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7a2e5f9c1b8"
down_revision: str | Sequence[str] | None = "b4d8f1a6c3e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sale_balance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tab_title", sa.String(length=120), nullable=False),
        sa.Column("folios", sa.JSON(), nullable=True),
        sa.Column("folio_month", sa.Date(), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_subtotal", sa.Numeric(14, 2), nullable=True),
        sa.Column("shipping_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("cost_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("sheet_sale_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("sheet_profit", sa.Numeric(14, 2), nullable=True),
        sa.Column("ledger_revenue", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("margin_pct", sa.Numeric(7, 2), nullable=True),
        sa.Column(
            "match_status",
            sa.String(length=20),
            nullable=False,
            server_default="orphan",
        ),
        sa.Column("recon_delta", sa.Numeric(14, 2), nullable=True),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint("tab_title", name="uq_sale_balance_tab_title"),
    )
    op.create_index("ix_sale_balance_id", "sale_balance", ["id"])
    op.create_index("ix_sale_balance_folio_month", "sale_balance", ["folio_month"])
    op.create_index("ix_sale_balance_match_status", "sale_balance", ["match_status"])


def downgrade() -> None:
    op.drop_index("ix_sale_balance_match_status", table_name="sale_balance")
    op.drop_index("ix_sale_balance_folio_month", table_name="sale_balance")
    op.drop_index("ix_sale_balance_id", table_name="sale_balance")
    op.drop_table("sale_balance")
