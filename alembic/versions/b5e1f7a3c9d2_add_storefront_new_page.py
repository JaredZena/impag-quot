"""add storefront_new_page

«Nueva página»: the admin creates a page of todoparaelcampo.com.mx from a
backend product. One row per new page (its storefront handle), with the
product that fills it and the store category it goes in. The storefront sync
adds the page while the row exists. No rows = nothing changes on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new table is touched.

Revision ID: b5e1f7a3c9d2
Revises: a7d2e9c4b1f8
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5e1f7a3c9d2"
down_revision: str | Sequence[str] | None = "a7d2e9c4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storefront_new_page",
        sa.Column("handle", sa.String(length=255), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category_slug", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("storefront_new_page")
