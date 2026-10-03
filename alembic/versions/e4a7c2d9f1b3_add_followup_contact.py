"""add followup_contact (Seguimiento del día)

Each WhatsApp follow-up sent from the "Seguimiento del día" list on Hoy:
who, why (open quote / last season's buyer / inactive customer) and the
outcome (enviado, respondió, venta, no le interesa). The list uses it to
not message the same person twice, and Hoy counts it in the HOY report.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: e4a7c2d9f1b3
Revises: c8e2f4a1b9d7
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a7c2d9f1b3"
down_revision: str | Sequence[str] | None = "c8e2f4a1b9d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "followup_contact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_key", sa.String(200), nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("quote.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="enviado"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_followup_contact_id", "followup_contact", ["id"])
    op.create_index(
        "ix_followup_contact_contact_key", "followup_contact", ["contact_key"]
    )
    op.create_index(
        "ix_followup_contact_created_at", "followup_contact", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_followup_contact_created_at", table_name="followup_contact")
    op.drop_index("ix_followup_contact_contact_key", table_name="followup_contact")
    op.drop_index("ix_followup_contact_id", table_name="followup_contact")
    op.drop_table("followup_contact")
