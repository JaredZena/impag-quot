"""add storefront_title to product

Customer-facing name for todoparaelcampo.com.mx, edited by the team on the
admin's "Precios de venta" screen. NULL keeps the storefront's own title, so
adding the column changes nothing on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new Product column is touched.

Revision ID: a8d2f4c61b97
Revises: c3a7e5d91f42
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8d2f4c61b97"
down_revision: str | Sequence[str] | None = "c3a7e5d91f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("storefront_title", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("product", "storefront_title")
