"""add quote follow-up tracking (last_followup_at, followup_count)

Idempotency + anti-spam guard for the stalled-quote WhatsApp/Task nudge sweep.

Revision ID: a1f7c3e9b2d4
Revises: c8b20cc4d90c
Create Date: 2026-07-20

Hand-written (autogenerate is NOT trusted on this DB — it drifts and would drop
the hybrid-search FTS column). Only the two new Quote columns are touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f7c3e9b2d4"
down_revision: Union[str, Sequence[str], None] = "c8b20cc4d90c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("quote", sa.Column("last_followup_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "quote",
        sa.Column("followup_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("quote", "followup_count")
    op.drop_column("quote", "last_followup_at")
