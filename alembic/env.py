"""Alembic environment — wired to the app's models + DATABASE_URL."""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the project importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Signal models.py to skip its module-level create_all so autogenerate can see
# new tables (must be set BEFORE importing models).
os.environ["ALEMBIC_RUNNING"] = "1"

from models import Base  # noqa: E402
import config as app_config  # noqa: E402  (app config module; NOT alembic's `context.config`)

alembic_config = context.config

# Use the real DB URL from the environment, never the alembic.ini placeholder.
if app_config.database_url:
    alembic_config.set_main_option("sqlalchemy.url", app_config.database_url)

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

target_metadata = Base.metadata

# Tables that exist in prod but are intentionally NOT modeled in models.py
# (legacy/backup/manually-managed). Excluded so autogenerate never proposes
# dropping them. Review any migration that touches these deliberately.
_UNMANAGED_TABLES = {
    "stock_backup", "classify_log", "product_variant",
    "spatial_ref_sys", "alembic_version",
}


# Objects created via raw SQL (not modeled) that are FUNCTIONALLY CRITICAL and
# must never be auto-dropped: the Spanish FTS generated column + the hybrid-search
# GIN/trigram indexes.
_PROTECTED_COLUMNS = {"chunk_tsv"}
_PROTECTED_INDEXES = {"ix_document_chunk_tsv", "ix_file_metadata_filename_trgm"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name in _UNMANAGED_TABLES:
        return False
    if type_ == "column" and name in _PROTECTED_COLUMNS:
        return False
    if type_ == "index" and name in _PROTECTED_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=alembic_config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_object=include_object, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
