"""add POS v2 columns (cost snapshot, vendedor, factura details)

POS bookkeeping pass: pos_sale gains vendedor, cost/margin rollups
(cost_total, margin_amount, cost_complete) and factura (CFDI) capture fields
(rfc, razon_social, uso_cfdi, cfdi_email); pos_sale_item gains the per-line
supplier cost snapshot (supplier_product_id, supplier_name, unit_cost,
cost_currency, exchange_rate, line_cost_mxn).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only columns on the two existing POS tables are touched.

Revision ID: f4a9c1d6b208
Revises: e3f8b2c7a914
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a9c1d6b208"
down_revision: str | Sequence[str] | None = "e3f8b2c7a914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pos_sale — vendedor, cost/margin rollups, factura (CFDI) details
    op.add_column("pos_sale", sa.Column("vendedor", sa.String(length=120), nullable=True))
    op.add_column("pos_sale", sa.Column("cost_total", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "pos_sale", sa.Column("margin_amount", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "pos_sale",
        sa.Column(
            "cost_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("pos_sale", sa.Column("rfc", sa.String(length=20), nullable=True))
    op.add_column(
        "pos_sale", sa.Column("razon_social", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "pos_sale", sa.Column("uso_cfdi", sa.String(length=10), nullable=True)
    )
    op.add_column(
        "pos_sale", sa.Column("cfdi_email", sa.String(length=255), nullable=True)
    )

    # pos_sale_item — supplier cost snapshot at sale time
    op.add_column(
        "pos_sale_item",
        sa.Column("supplier_product_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pos_sale_item_supplier_product_id",
        "pos_sale_item",
        "supplier_product",
        ["supplier_product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "pos_sale_item", sa.Column("supplier_name", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "pos_sale_item", sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "pos_sale_item", sa.Column("cost_currency", sa.String(length=3), nullable=True)
    )
    op.add_column(
        "pos_sale_item", sa.Column("exchange_rate", sa.Numeric(10, 4), nullable=True)
    )
    op.add_column(
        "pos_sale_item", sa.Column("line_cost_mxn", sa.Numeric(12, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("pos_sale_item", "line_cost_mxn")
    op.drop_column("pos_sale_item", "exchange_rate")
    op.drop_column("pos_sale_item", "cost_currency")
    op.drop_column("pos_sale_item", "unit_cost")
    op.drop_column("pos_sale_item", "supplier_name")
    op.drop_constraint(
        "fk_pos_sale_item_supplier_product_id", "pos_sale_item", type_="foreignkey"
    )
    op.drop_column("pos_sale_item", "supplier_product_id")

    op.drop_column("pos_sale", "cfdi_email")
    op.drop_column("pos_sale", "uso_cfdi")
    op.drop_column("pos_sale", "razon_social")
    op.drop_column("pos_sale", "rfc")
    op.drop_column("pos_sale", "cost_complete")
    op.drop_column("pos_sale", "margin_amount")
    op.drop_column("pos_sale", "cost_total")
    op.drop_column("pos_sale", "vendedor")
