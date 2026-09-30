"""add online_sale JSON column to product

The "Vender en línea" switch for todoparaelcampo.com.mx, edited by the team in
the admin: {"enabled", "unit_label", "delivery", "min_qty", "max_qty",
"stock_status", "updated_by", "updated_at"}. NULL = never configured in the
admin, so the storefront sync keeps using its own config and adding the
column changes nothing on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only the one new Product column is touched.

Revision ID: b3f8d1e6a4c2
Revises: f1c4a9e2d756
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3f8d1e6a4c2"
down_revision: str | Sequence[str] | None = "f1c4a9e2d756"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("online_sale", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("product", "online_sale")
