"""add storefront_description to product

"Especificaciones en la página": the description rows of the product's page
on todoparaelcampo.com.mx, edited by the team on the admin's product screen as
plain text, one row per line (the same "Título:" / "Clave: valor" / "- viñeta"
rows the page renders). NULL keeps the page's own description, so adding the
column changes nothing on the site.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new Product column is touched.

Revision ID: c6a2e8f4d1b9
Revises: b4e8d1a6c2f7
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c6a2e8f4d1b9"
down_revision: str | Sequence[str] | None = "b4e8d1a6c2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("product", sa.Column("storefront_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("product", "storefront_description")
