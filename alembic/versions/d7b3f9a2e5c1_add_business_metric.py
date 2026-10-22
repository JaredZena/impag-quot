"""add business_metric

Numbers from outside the app for the "Cómo vamos" panel on /hoy: Google
Business Profile monthly report (views, calls, directions, website clicks),
Google reviews, and Search Console clicks per day. Filled by
POST /jobs/google-metrics-sync (services/business_metrics.py).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md).
Only the one new table is created.

Revision ID: d7b3f9a2e5c1
Revises: c6a2e8f4d1b9
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7b3f9a2e5c1"
down_revision: str | Sequence[str] | None = "c6a2e8f4d1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_metric",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("ref", sa.String(length=120), nullable=False, server_default=""),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "source",
            "metric",
            "period",
            "period_start",
            "ref",
            name="uq_business_metric_key",
        ),
    )
    op.create_index("ix_business_metric_id", "business_metric", ["id"])
    op.create_index("ix_business_metric_source", "business_metric", ["source"])
    op.create_index(
        "ix_business_metric_period_start", "business_metric", ["period_start"]
    )


def downgrade() -> None:
    op.drop_index("ix_business_metric_period_start", table_name="business_metric")
    op.drop_index("ix_business_metric_source", table_name="business_metric")
    op.drop_index("ix_business_metric_id", table_name="business_metric")
    op.drop_table("business_metric")
