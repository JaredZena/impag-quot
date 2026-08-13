"""add images JSON column to product

Stores a list of R2 object keys (bucket impag-files, prefix
product-images/{product_id}/). List order is display order; first key is the
primary image.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new Product column is touched.

Revision ID: e9c4d7a51f38
Revises: b7e4a2c91d05
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9c4d7a51f38"
down_revision: str | Sequence[str] | None = "b7e4a2c91d05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("images", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("product", "images")
