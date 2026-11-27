"""add storefront_page_category

«Colección en la tienda»: the admin moves an existing page of
todoparaelcampo.com.mx to another collection. One row per moved page (its
storefront handle) with the collection it goes in. The storefront sync
re-files the page on the next publish. No rows = nothing changes on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new table is touched.

Revision ID: d4f2a8c6e1b9
Revises: c3e8f1a5d7b2
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f2a8c6e1b9"
down_revision: str | Sequence[str] | None = "c3e8f1a5d7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storefront_page_category",
        sa.Column("handle", sa.String(length=255), primary_key=True),
        sa.Column("category_slug", sa.String(length=100), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("storefront_page_category")
