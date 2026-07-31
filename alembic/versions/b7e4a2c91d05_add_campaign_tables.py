"""add campaign tables (campaign, campaign_phase, campaign_item)

Hand-written (no autogenerate — see MIGRATIONS.md for the drift caveat).
Creates the three campaign-planner tables plus indexes on the FK columns.

Revision ID: b7e4a2c91d05
Revises: a1f7c3e9b2d4
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4a2c91d05"
down_revision: str | Sequence[str] | None = "a1f7c3e9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("size", sa.String(), nullable=False, server_default="mediana"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("goals", sa.JSON(), nullable=True),
        sa.Column("key_messages", sa.JSON(), nullable=True),
        sa.Column("channel_plan", sa.JSON(), nullable=True),
        sa.Column("research", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("generation_model", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_campaign_id"), "campaign", ["id"], unique=False)

    op.create_table(
        "campaign_phase",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaign.id"],
            name="fk_campaign_phase_campaign_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_campaign_phase_id"), "campaign_phase", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_campaign_phase_campaign_id"),
        "campaign_phase",
        ["campaign_id"],
        unique=False,
    )

    op.create_table(
        "campaign_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("scheduled_date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="planned"),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("social_post_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaign.id"],
            name="fk_campaign_item_campaign_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["phase_id"],
            ["campaign_phase.id"],
            name="fk_campaign_item_phase_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(op.f("ix_campaign_item_id"), "campaign_item", ["id"], unique=False)
    op.create_index(
        op.f("ix_campaign_item_campaign_id"),
        "campaign_item",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_campaign_item_phase_id"),
        "campaign_item",
        ["phase_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_campaign_item_phase_id"), table_name="campaign_item")
    op.drop_index(op.f("ix_campaign_item_campaign_id"), table_name="campaign_item")
    op.drop_index(op.f("ix_campaign_item_id"), table_name="campaign_item")
    op.drop_table("campaign_item")

    op.drop_index(op.f("ix_campaign_phase_campaign_id"), table_name="campaign_phase")
    op.drop_index(op.f("ix_campaign_phase_id"), table_name="campaign_phase")
    op.drop_table("campaign_phase")

    op.drop_index(op.f("ix_campaign_id"), table_name="campaign")
    op.drop_table("campaign")
