"""add tool + tool_movement (inventario de herramientas)

Internal tools/consumables register, separate from the sale catalog so tools
never leak into POS, quotes or the storefront feed. Replaces the HERRAMIENTAS
tab of Operaciones_Comerciales_IMPAG (scripts/import_tools_from_sheet.py
imports it, keyed on sheet_no). tool_movement is the lifecycle audit trail
(compra, salida a obra, regreso, baja...).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the two new tables and their indexes are created.

Revision ID: c3a7e5d91f42
Revises: b9e1d4a7c623
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3a7e5d91f42"
down_revision: str | Sequence[str] | None = "b9e1d4a7c623"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="herramienta"),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(20), nullable=False, server_default="PIEZA"),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=True),
        sa.Column("supplier_name", sa.String(200), nullable=True),
        sa.Column("invoice_ref", sa.String(120), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="en_local"),
        sa.Column("location", sa.String(60), nullable=True),
        sa.Column("holder", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_reason", sa.String(300), nullable=True),
        sa.Column("sheet_no", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.UniqueConstraint("sheet_no", name="uq_tool_sheet_no"),
    )
    op.create_index("ix_tool_id", "tool", ["id"])
    op.create_index("ix_tool_status", "tool", ["status"])

    op.create_table(
        "tool_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tool_id",
            sa.Integer(),
            sa.ForeignKey(
                "tool.id", name="fk_tool_movement_tool_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=True),
        sa.Column("holder", sa.String(200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(120), nullable=True),
    )
    op.create_index("ix_tool_movement_id", "tool_movement", ["id"])
    op.create_index("ix_tool_movement_tool_id", "tool_movement", ["tool_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_movement_tool_id", table_name="tool_movement")
    op.drop_index("ix_tool_movement_id", table_name="tool_movement")
    op.drop_table("tool_movement")
    op.drop_index("ix_tool_status", table_name="tool")
    op.drop_index("ix_tool_id", table_name="tool")
    op.drop_table("tool")
