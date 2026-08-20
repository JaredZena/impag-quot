-- Sales ledger table ("sale") — mirror of the VENTAS Google Sheet.
-- Operational snapshot only, NOT accounting books.
--
-- Canonical migration: alembic revision b4d8f1a6c3e7 (add_sale_table).
-- Prefer:   venv/bin/alembic upgrade head
-- If this SQL is applied by hand instead, stamp alembic afterwards so the
-- history stays consistent:
--   venv/bin/alembic stamp b4d8f1a6c3e7
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS sale (
    id SERIAL PRIMARY KEY,
    sheet_tab VARCHAR(20) NOT NULL,
    source_row INTEGER NOT NULL,
    sale_date DATE,
    month_label VARCHAR(20),
    customer_name VARCHAR(200),
    customer_id INTEGER REFERENCES customer(id) ON DELETE SET NULL,
    description TEXT,
    unit VARCHAR(30),
    quantity NUMERIC(12, 2),
    unit_price NUMERIC(12, 2),
    amount NUMERIC(12, 2),
    concept VARCHAR(100),
    payment_method VARCHAR(30),
    delivery_place VARCHAR(200),
    reference VARCHAR(200),
    folio VARCHAR(40),
    delivery_status VARCHAR(30),
    requires_invoice BOOLEAN,
    registered BOOLEAN,
    quarantined BOOLEAN NOT NULL DEFAULT FALSE,
    quarantine_reason VARCHAR(200),
    imported_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_sale_tab_row UNIQUE (sheet_tab, source_row)
);

CREATE INDEX IF NOT EXISTS ix_sale_id ON sale (id);
CREATE INDEX IF NOT EXISTS ix_sale_sale_date ON sale (sale_date);
CREATE INDEX IF NOT EXISTS ix_sale_customer_id ON sale (customer_id);
CREATE INDEX IF NOT EXISTS ix_sale_folio ON sale (folio);
