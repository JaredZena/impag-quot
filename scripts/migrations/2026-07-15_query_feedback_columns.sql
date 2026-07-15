-- Additive migration: query-logging + feedback columns.
-- create_all never ALTERs existing tables, so model columns must be added here.
-- complexity_tier existed in models.py but was MISSING in prod (every /query
-- INSERT failed on it since it was added to the model).
ALTER TABLE query ADD COLUMN IF NOT EXISTS complexity_tier VARCHAR(20);
ALTER TABLE query ADD COLUMN IF NOT EXISTS user_email VARCHAR(255);
ALTER TABLE query ADD COLUMN IF NOT EXISTS retrieved_chunk_ids JSON;
ALTER TABLE query ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
ALTER TABLE query ADD COLUMN IF NOT EXISTS feedback SMALLINT;
ALTER TABLE query ADD COLUMN IF NOT EXISTS feedback_text TEXT;
ALTER TABLE query ADD COLUMN IF NOT EXISTS feedback_by VARCHAR(255);
ALTER TABLE query ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ;
