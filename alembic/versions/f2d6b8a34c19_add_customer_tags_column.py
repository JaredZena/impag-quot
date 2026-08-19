"""add tags JSON column to customer

Stores a list of lowercase slug strings (e.g. ["sembrando-vida"]) used for
segmenting the customer directory (filters + stats).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new Customer column is touched.

Revision ID: f2d6b8a34c19
Revises: e9c4d7a51f38
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2d6b8a34c19"
down_revision: str | Sequence[str] | None = "e9c4d7a51f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customer", sa.Column("tags", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("customer", "tags")
