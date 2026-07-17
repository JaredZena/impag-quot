# Database migrations (Alembic)

Alembic is the **source of truth for schema changes**. Do NOT hand-apply SQL or
rely on `create_all` for new schema. (`create_all` still runs on app startup as a
transitional backstop; it is skipped when Alembic imports models via
`ALEMBIC_RUNNING`.) The old `run_migration.py` was sqlite-only and is removed.

DB: Neon Postgres (`DATABASE_URL`). Alembic reads that same env var (see
`alembic/env.py`).

## Making a schema change

```bash
# 1. Edit models.py (add table/column/index)
# 2. Generate a migration
venv/bin/alembic revision --autogenerate -m "add X to Y"
# 3. *** REVIEW THE GENERATED FILE — THIS IS NOT OPTIONAL *** (see warning below)
# 4. Apply
venv/bin/alembic upgrade head
# 5. Commit models.py + the new alembic/versions/*.py together
```

Governance: **one migration in flight at a time**; every migration file is
reviewed before `upgrade`; commit the model change and its migration together.

## ⚠️ Autogenerate is a loaded gun here — ALWAYS review before upgrade

The prod schema has **drifted** from `models.py` (years of hand-applied SQL), so
`--autogenerate` proposes many **destructive** ops that must be DELETED from the
generated file before applying. If you blind-apply, you will break production.

Already guarded in `alembic/env.py` (never proposed for drop):
- Unmanaged tables: `stock_backup`, `classify_log`, `product_variant`, `spatial_ref_sys`.
- Protected FTS objects: column `chunk_tsv`, indexes `ix_document_chunk_tsv`,
  `ix_file_metadata_filename_trgm` (dropping these breaks hybrid search).

Still surfaced by autogenerate as spurious "removals" (DB has them, models.py
doesn't) — **strip these from any generated migration unless you truly intend them:**
- Many `idx_*` performance indexes on balance / balance_item / document_chunk /
  file_metadata (created by hand; not modeled).
- `balance_item` column nullability differences.

Rule of thumb: a generated migration should contain ONLY the change you just made
to `models.py`. Delete every `drop_*` / `alter_column` you did not intend.

## Reducing the drift (ongoing)

Each time you touch a table, add its real indexes/columns to `models.py` so the
diff shrinks. Long-term goal: `--autogenerate` on an unchanged `models.py`
produces an empty migration.

## Baseline

`ed11e2a7033d` = baseline (empty); prod was `alembic stamp`ed to it on
2026-07-16. Migrations of record so far this year (pre-Alembic, in
`scripts/migrations/*.sql`) were already applied by hand; they are historical.
