-- In-app roadmap / progress tracker.
CREATE TABLE IF NOT EXISTS roadmap_item (
    id SERIAL PRIMARY KEY,
    phase INTEGER NOT NULL DEFAULT 0,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    need VARCHAR(100),
    effort VARCHAR(20),
    impact VARCHAR(20),
    status VARCHAR(20) DEFAULT 'planned',
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_roadmap_item_status ON roadmap_item(status);
