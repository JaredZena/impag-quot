"""add storefront_page_visibility

«Mostrar en la tienda»: the admin switch that takes a page of
todoparaelcampo.com.mx off the store (and puts it back) without a code
change. One row per storefront handle; most store pages have no backend
product, so the key is the handle, not a product id. No rows = every page
shown, so creating the table changes nothing on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new table is touched.

Revision ID: e3b7c5a9d1f4
Revises: a7c3e9d2b5f1
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3b7c5a9d1f4"
down_revision: str | Sequence[str] | None = "a7c3e9d2b5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storefront_page_visibility",
        sa.Column("handle", sa.String(length=255), primary_key=True),
        sa.Column(
            "hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("storefront_page_visibility")
