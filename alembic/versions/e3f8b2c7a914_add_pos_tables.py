"""add POS tables (pos_sale, pos_sale_item, cash sessions, stock movements)

Punto de Venta: pos_sale/pos_sale_item are the source of truth for in-store
tickets (also projected into the `sale` ledger with sheet_tab='POS');
cash_session/cash_movement track the cash drawer; stock_movement audits
product.stock changes; pos_folio_counter mints race-safe folios.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the six new tables + their indexes are touched.

Revision ID: e3f8b2c7a914
Revises: d7a2e5f9c1b8
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f8b2c7a914"
down_revision: str | Sequence[str] | None = "d7a2e5f9c1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cash_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch", sa.String(length=4), nullable=False, server_default="DGO"),
        sa.Column(
            "status", sa.String(length=10), nullable=False, server_default="abierta"
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("opened_by", sa.String(length=120), nullable=True),
        sa.Column(
            "opening_float", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=120), nullable=True),
        sa.Column("expected_cash", sa.Numeric(12, 2), nullable=True),
        sa.Column("counted_cash", sa.Numeric(12, 2), nullable=True),
        sa.Column("difference", sa.Numeric(12, 2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_cash_session_id", "cash_session", ["id"])

    op.create_table(
        "pos_sale",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("folio", sa.String(length=40), nullable=False),
        sa.Column("branch", sa.String(length=4), nullable=False, server_default="DGO"),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("customer_name", sa.String(length=200), nullable=True),
        sa.Column("customer_phone", sa.String(length=20), nullable=True),
        sa.Column("payment_method", sa.String(length=30), nullable=False),
        sa.Column("amount_tendered", sa.Numeric(12, 2), nullable=True),
        sa.Column("change_given", sa.Numeric(12, 2), nullable=True),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("iva_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "requires_invoice",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("delivery_place", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="completada"
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.String(length=120), nullable=True),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("cash_session_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["cash_session_id"], ["cash_session.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_pos_sale_id", "pos_sale", ["id"])
    op.create_index("ix_pos_sale_folio", "pos_sale", ["folio"])
    op.create_index("ix_pos_sale_sale_date", "pos_sale", ["sale_date"])
    op.create_index("ix_pos_sale_customer_id", "pos_sale", ["customer_id"])

    op.create_table(
        "pos_sale_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pos_sale_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("iva", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["pos_sale_id"], ["pos_sale.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_pos_sale_item_id", "pos_sale_item", ["id"])
    op.create_index("ix_pos_sale_item_pos_sale_id", "pos_sale_item", ["pos_sale_id"])

    op.create_table(
        "cash_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cash_session_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("pos_sale_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(
            ["cash_session_id"], ["cash_session.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["pos_sale_id"], ["pos_sale.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_cash_movement_id", "cash_movement", ["id"])
    op.create_index(
        "ix_cash_movement_cash_session_id", "cash_movement", ["cash_session_id"]
    )

    op.create_table(
        "stock_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("pos_sale_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pos_sale_id"], ["pos_sale.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_stock_movement_id", "stock_movement", ["id"])
    op.create_index("ix_stock_movement_product_id", "stock_movement", ["product_id"])

    op.create_table(
        "pos_folio_counter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month_year", sa.String(length=4), nullable=False),
        sa.Column("branch", sa.String(length=4), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("month_year", "branch", name="uq_pos_folio_counter"),
    )
    op.create_index("ix_pos_folio_counter_id", "pos_folio_counter", ["id"])


def downgrade() -> None:
    op.drop_index("ix_pos_folio_counter_id", table_name="pos_folio_counter")
    op.drop_table("pos_folio_counter")
    op.drop_index("ix_stock_movement_product_id", table_name="stock_movement")
    op.drop_index("ix_stock_movement_id", table_name="stock_movement")
    op.drop_table("stock_movement")
    op.drop_index("ix_cash_movement_cash_session_id", table_name="cash_movement")
    op.drop_index("ix_cash_movement_id", table_name="cash_movement")
    op.drop_table("cash_movement")
    op.drop_index("ix_pos_sale_item_pos_sale_id", table_name="pos_sale_item")
    op.drop_index("ix_pos_sale_item_id", table_name="pos_sale_item")
    op.drop_table("pos_sale_item")
    op.drop_index("ix_pos_sale_customer_id", table_name="pos_sale")
    op.drop_index("ix_pos_sale_sale_date", table_name="pos_sale")
    op.drop_index("ix_pos_sale_folio", table_name="pos_sale")
    op.drop_index("ix_pos_sale_id", table_name="pos_sale")
    op.drop_table("pos_sale")
    op.drop_index("ix_cash_session_id", table_name="cash_session")
    op.drop_table("cash_session")
