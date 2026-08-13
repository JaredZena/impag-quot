-- Add product.images: list of R2 object keys (JSON array).
-- List order is display order; first key is the primary image.
--
-- Equivalent to alembic revision e9c4d7a51f38 (alembic/versions/
-- e9c4d7a51f38_add_product_images_column.py). Apply ONE of the two, not both;
-- if this SQL is applied by hand, `alembic stamp e9c4d7a51f38` afterwards so
-- the alembic history stays in sync (see MIGRATIONS.md).
--
-- Idempotent: safe to re-run.

ALTER TABLE product ADD COLUMN IF NOT EXISTS images JSON;
