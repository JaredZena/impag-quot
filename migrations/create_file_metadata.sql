BEGIN;

CREATE TABLE IF NOT EXISTS file_metadata (
    id SERIAL PRIMARY KEY,
    file_key VARCHAR(500) NOT NULL UNIQUE,
    original_filename VARCHAR(500) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size_bytes BIGINT NOT NULL,

    category VARCHAR(50) NOT NULL DEFAULT 'general',
    description TEXT,
    tags VARCHAR(500),

    supplier_id INTEGER REFERENCES supplier(id) ON DELETE SET NULL,
    quotation_id INTEGER REFERENCES quotation(id) ON DELETE SET NULL,
    task_id INTEGER REFERENCES task(id) ON DELETE SET NULL,

    uploaded_by_email VARCHAR(255) NOT NULL,
    uploaded_by_name VARCHAR(200),
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_file_metadata_category ON file_metadata(category);
CREATE INDEX IF NOT EXISTS idx_file_metadata_supplier_id ON file_metadata(supplier_id);
CREATE INDEX IF NOT EXISTS idx_file_metadata_quotation_id ON file_metadata(quotation_id);
CREATE INDEX IF NOT EXISTS idx_file_metadata_task_id ON file_metadata(task_id);
CREATE INDEX IF NOT EXISTS idx_file_metadata_uploaded_by ON file_metadata(uploaded_by_email);
CREATE INDEX IF NOT EXISTS idx_file_metadata_created_at ON file_metadata(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_metadata_archived_at ON file_metadata(archived_at);

COMMIT;
