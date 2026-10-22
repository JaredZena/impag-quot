"""add app_setting, ai_usage, apoyo, apoyo_run, agro_project, agro_project_message

The storefront's "Apoyos gubernamentales" (/apoyos) and "Agroproyectos"
(/agroproyectos) features, and the admin-editable settings and AI usage/cost
ledger they share (services/app_settings.py, services/ai_usage.py).

Hand-written (autogenerate is NOT trusted on this DB — see MIGRATIONS.md for
the drift caveat). Only new tables; no existing table is touched.

Revision ID: a7c3e9d2b5f1
Revises: c6a2e8f4d1b9
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e9d2b5f1"
down_revision: str | Sequence[str] | None = "c6a2e8f4d1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=60), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        _ts("updated_at", server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        _ts("created_at", server_default=sa.func.now()),
        sa.Column("feature", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("requested_model", sa.String(length=60), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cache_creation_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "web_search_requests", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "web_fetch_requests", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("ref_type", sa.String(length=30), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_ai_usage_id", "ai_usage", ["id"])
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"])
    op.create_index("ix_ai_usage_feature", "ai_usage", ["feature"])
    op.create_index("ix_ai_usage_ref_id", "ai_usage", ["ref_id"])

    op.create_table(
        "apoyo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="draft"
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("program", sa.String(length=300), nullable=True),
        sa.Column("organism", sa.String(length=200), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=True),
        sa.Column("states", sa.JSON(), nullable=True),
        sa.Column("sectors", sa.JSON(), nullable=True),
        sa.Column("support_types", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("beneficiaries", sa.Text(), nullable=True),
        sa.Column("amount_text", sa.String(length=500), nullable=True),
        sa.Column("amount_max_mxn", sa.Numeric(14, 2), nullable=True),
        sa.Column("requirements", sa.JSON(), nullable=True),
        sa.Column("how_to_apply", sa.JSON(), nullable=True),
        sa.Column("opens_at", sa.Date(), nullable=True),
        sa.Column("closes_at", sa.Date(), nullable=True),
        sa.Column("deadline_text", sa.String(length=300), nullable=True),
        sa.Column(
            "permanent", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("category_slugs", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("ai_notes", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(length=200), nullable=True),
        sa.Column(
            "featured", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("discovered_run_id", sa.Integer(), nullable=True),
        _ts("first_seen_at", nullable=True),
        _ts("last_seen_at", nullable=True),
        _ts("verified_at", nullable=True),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        _ts("published_at", nullable=True),
        _ts("created_at", server_default=sa.func.now()),
        _ts("updated_at", server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_apoyo_id", "apoyo", ["id"])
    op.create_index("ix_apoyo_slug", "apoyo", ["slug"], unique=True)
    op.create_index("ix_apoyo_status", "apoyo", ["status"])
    op.create_index("ix_apoyo_fingerprint", "apoyo", ["fingerprint"])

    op.create_table(
        "apoyo_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trigger", sa.String(length=20), nullable=False, server_default="schedule"
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("topics", sa.JSON(), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("web_searches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        _ts("created_at", server_default=sa.func.now()),
        _ts("started_at", nullable=True),
        _ts("finished_at", nullable=True),
    )
    op.create_index("ix_apoyo_run_id", "apoyo_run", ["id"])
    op.create_index("ix_apoyo_run_status", "apoyo_run", ["status"])
    op.create_index("ix_apoyo_run_created_at", "apoyo_run", ["created_at"])

    op.create_table(
        "agro_project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(length=36), nullable=False),
        sa.Column("ref", sa.String(length=30), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "lead_status", sa.String(length=20), nullable=False, server_default="nuevo"
        ),
        sa.Column("producer_type", sa.String(length=20), nullable=True),
        sa.Column("idea", sa.Text(), nullable=False),
        sa.Column("current_operation", sa.Text(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("contact_name", sa.String(length=200), nullable=True),
        sa.Column("contact_phone", sa.String(length=30), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=60), nullable=True),
        sa.Column("municipio", sa.String(length=120), nullable=True),
        sa.Column("budget_mxn", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "budget_unknown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "wants_financing",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("project_kind", sa.String(length=60), nullable=True),
        sa.Column("sector", sa.String(length=30), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("investment_min_mxn", sa.Numeric(14, 2), nullable=True),
        sa.Column("investment_max_mxn", sa.Numeric(14, 2), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("products", sa.JSON(), nullable=True),
        sa.Column("irrigation", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(length=120), nullable=True),
        sa.Column(
            "quote_id",
            sa.Integer(),
            sa.ForeignKey("quote.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("questions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events", sa.JSON(), nullable=True),
        _ts("contact_requested_at", nullable=True),
        sa.Column("contact_request", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("generation_ms", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("utm", sa.JSON(), nullable=True),
        sa.Column("staff_notes", sa.Text(), nullable=True),
        sa.Column("assigned_to", sa.String(length=255), nullable=True),
        _ts("created_at", server_default=sa.func.now()),
        _ts("updated_at", server_default=sa.func.now()),
    )
    op.create_index("ix_agro_project_id", "agro_project", ["id"])
    op.create_index("ix_agro_project_token", "agro_project", ["token"], unique=True)
    op.create_unique_constraint("uq_agro_project_ref", "agro_project", ["ref"])
    op.create_index("ix_agro_project_status", "agro_project", ["status"])
    op.create_index("ix_agro_project_lead_status", "agro_project", ["lead_status"])
    op.create_index("ix_agro_project_contact_phone", "agro_project", ["contact_phone"])
    op.create_index("ix_agro_project_state", "agro_project", ["state"])
    op.create_index("ix_agro_project_project_kind", "agro_project", ["project_kind"])
    op.create_index("ix_agro_project_sector", "agro_project", ["sector"])
    op.create_index("ix_agro_project_created_at", "agro_project", ["created_at"])

    op.create_table(
        "agro_project_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("agro_project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        _ts("created_at", server_default=sa.func.now()),
    )
    op.create_index("ix_agro_project_message_id", "agro_project_message", ["id"])
    op.create_index(
        "ix_agro_project_message_project_id", "agro_project_message", ["project_id"]
    )


def downgrade() -> None:
    op.drop_table("agro_project_message")
    op.drop_table("agro_project")
    op.drop_table("apoyo_run")
    op.drop_table("apoyo")
    op.drop_table("ai_usage")
    op.drop_table("app_setting")
