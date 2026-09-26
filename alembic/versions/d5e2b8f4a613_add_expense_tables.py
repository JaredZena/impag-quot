"""add expense_concept + monthly_expense (punto de equilibrio)

Monthly fixed-cost register behind the admin's "Punto de equilibrio" page:
expense_concept is the recurring template (renta, sueldos, camioneta...),
monthly_expense the per-month lines (with paid flag, so unpaid salaries show
up as adeudos). Break-even = fixed costs / measured gross margin.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the two new tables and their indexes are created.

Revision ID: d5e2b8f4a613
Revises: a8d2f4c61b97
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e2b8f4a613"
down_revision: str | Sequence[str] | None = "a8d2f4c61b97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "expense_concept",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "category", sa.String(20), nullable=False, server_default="operativo"
        ),
        sa.Column(
            "default_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("name", name="uq_expense_concept_name"),
    )
    op.create_index("ix_expense_concept_id", "expense_concept", ["id"])

    op.create_table(
        "monthly_expense",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "concept_id",
            sa.Integer(),
            sa.ForeignKey("expense_concept.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "category", sa.String(20), nullable=False, server_default="operativo"
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "paid", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.UniqueConstraint(
            "month", "concept_id", name="uq_monthly_expense_month_concept"
        ),
    )
    op.create_index("ix_monthly_expense_id", "monthly_expense", ["id"])
    op.create_index("ix_monthly_expense_month", "monthly_expense", ["month"])
    op.create_index("ix_monthly_expense_concept_id", "monthly_expense", ["concept_id"])


def downgrade() -> None:
    op.drop_index("ix_monthly_expense_concept_id", table_name="monthly_expense")
    op.drop_index("ix_monthly_expense_month", table_name="monthly_expense")
    op.drop_index("ix_monthly_expense_id", table_name="monthly_expense")
    op.drop_table("monthly_expense")
    op.drop_index("ix_expense_concept_id", table_name="expense_concept")
    op.drop_table("expense_concept")
