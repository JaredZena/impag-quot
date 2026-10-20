"""widen quote.quote_number to 30 characters

The team sometimes reuses a COT-IMPAG folio for a different customer (the
template gets copied). quote_number is unique, so the second quote needs a
suffix: COT-IMPAG-320926CDMX-2. The longest folio already fills 20 characters,
so the column grows to 30. Widening a varchar is a catalog-only change in
Postgres (no table rewrite).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: b4e8d1a6c2f7
Revises: d2b7e9a4c815
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e8d1a6c2f7"
down_revision: str | Sequence[str] | None = "d2b7e9a4c815"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "quote",
        "quote_number",
        type_=sa.String(30),
        existing_type=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "quote",
        "quote_number",
        type_=sa.String(20),
        existing_type=sa.String(30),
        existing_nullable=False,
    )
