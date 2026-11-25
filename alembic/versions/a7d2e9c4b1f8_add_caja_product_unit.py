"""add CAJA to the productunit enum

Hernán asked for kit, caja and paquete as product units. KIT and PAQUETE
were already in the Postgres enum (migrations/add_unit_types_embedded_columns.sql);
only CAJA is new. Adding an enum value touches no rows.

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).

Revision ID: a7d2e9c4b1f8
Revises: f4c8a2d6e1b3
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7d2e9c4b1f8"
down_revision: str | Sequence[str] | None = "f4c8a2d6e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE productunit ADD VALUE IF NOT EXISTS 'CAJA'")


def downgrade() -> None:
    # Postgres can't drop one value from an enum; an unused CAJA is harmless.
    pass
