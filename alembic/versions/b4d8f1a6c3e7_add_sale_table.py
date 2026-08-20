"""add sale table (sales ledger mirror of the VENTAS Google Sheet)

One row per spreadsheet row, keyed (sheet_tab, source_row) so re-syncs upsert
in place. Operational snapshot only — NOT accounting books.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new table + its indexes are touched.

Revision ID: b4d8f1a6c3e7
Revises: f2d6b8a34c19
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4d8f1a6c3e7"
down_revision: str | Sequence[str] | None = "f2d6b8a34c19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sale",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sheet_tab", sa.String(length=20), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=True),
        sa.Column("month_label", sa.String(length=20), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("concept", sa.String(length=100), nullable=True),
        sa.Column("payment_method", sa.String(length=30), nullable=True),
        sa.Column("delivery_place", sa.String(length=200), nullable=True),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("folio", sa.String(length=40), nullable=True),
        sa.Column("delivery_status", sa.String(length=30), nullable=True),
        sa.Column("requires_invoice", sa.Boolean(), nullable=True),
        sa.Column("registered", sa.Boolean(), nullable=True),
        sa.Column(
            "quarantined", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("quarantine_reason", sa.String(length=200), nullable=True),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("sheet_tab", "source_row", name="uq_sale_tab_row"),
    )
    op.create_index("ix_sale_id", "sale", ["id"])
    op.create_index("ix_sale_sale_date", "sale", ["sale_date"])
    op.create_index("ix_sale_customer_id", "sale", ["customer_id"])
    op.create_index("ix_sale_folio", "sale", ["folio"])


def downgrade() -> None:
    op.drop_index("ix_sale_folio", table_name="sale")
    op.drop_index("ix_sale_customer_id", table_name="sale")
    op.drop_index("ix_sale_sale_date", table_name="sale")
    op.drop_index("ix_sale_id", table_name="sale")
    op.drop_table("sale")
