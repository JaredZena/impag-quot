"""add Siguiente paso columns to quote

Per open quote: what the team does next (next_action), who does it
(next_action_owner, a first name: "Hernán", "JD", ...) and by when
(next_action_due). Set from the admin (PUT /quotes/{id}/next-action) and by
scripts/load_next_actions.py; the quote list filters and sorts on the due
date, hence the index.

All columns nullable; existing quotes have no next step.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: d2b7e9a4c815
Revises: e4a7c2d9f1b3
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2b7e9a4c815"
down_revision: str | Sequence[str] | None = "e4a7c2d9f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quote", sa.Column("next_action", sa.String(200), nullable=True))
    op.add_column("quote", sa.Column("next_action_owner", sa.String(50), nullable=True))
    op.add_column("quote", sa.Column("next_action_due", sa.Date(), nullable=True))
    op.create_index("ix_quote_next_action_due", "quote", ["next_action_due"])


def downgrade() -> None:
    op.drop_index("ix_quote_next_action_due", table_name="quote")
    op.drop_column("quote", "next_action_due")
    op.drop_column("quote", "next_action_owner")
    op.drop_column("quote", "next_action")
