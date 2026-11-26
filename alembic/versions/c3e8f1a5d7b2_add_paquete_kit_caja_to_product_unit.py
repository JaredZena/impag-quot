"""add PAQUETE, KIT and CAJA to product_unit, the type product.unit really uses

a7d2e9c4b1f8 added CAJA to `productunit`, the name models.py implies, but the
prod column product.unit is typed `product_unit` (pieza..kg, PIEZA..KG only).
So every save with Paquete, Kit or Caja 500'd: "invalid input value for enum
product_unit". On 11-25/26 Hernán hit it 21 times and saved those products as
PIEZA instead. Adding enum values touches no rows.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: c3e8f1a5d7b2
Revises: b5e1f7a3c9d2
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3e8f1a5d7b2"
down_revision: str | Sequence[str] | None = "b5e1f7a3c9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for value in ("PAQUETE", "KIT", "CAJA"):
        op.execute(f"ALTER TYPE product_unit ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres can't drop one value from an enum; unused values are harmless.
    pass
