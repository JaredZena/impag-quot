-- Add customer.tags: list of lowercase slug strings (JSON array),
-- e.g. ["sembrando-vida"]. Used for directory filters + stats.
--
-- Equivalent to alembic revision f2d6b8a34c19 (alembic/versions/
-- f2d6b8a34c19_add_customer_tags_column.py). Apply ONE of the two, not both;
-- if this SQL is applied by hand, `alembic stamp f2d6b8a34c19` afterwards so
-- the alembic history stays in sync (see MIGRATIONS.md).
--
-- Idempotent: safe to re-run.

ALTER TABLE customer ADD COLUMN IF NOT EXISTS tags JSON;
