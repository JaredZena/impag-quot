"""add storefront_page_link

«Ligar producto»: the admin picks which backend product prices a page of
todoparaelcampo.com.mx, instead of the storefront's name-matched
scripts/product-mapping.json. One row per storefront handle; product_id NULL
means the admin removed a wrong match (the page has no product in the
system). No rows = the mapping file decides every page, so creating the table
changes nothing on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new table is touched.

Revision ID: f4c8a2d6e1b3
Revises: e3b7c5a9d1f4
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4c8a2d6e1b3"
down_revision: str | Sequence[str] | None = "e3b7c5a9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storefront_page_link",
        sa.Column("handle", sa.String(length=255), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("storefront_page_link")
