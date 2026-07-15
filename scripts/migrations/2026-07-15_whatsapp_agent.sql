-- WhatsApp sales agent (human-in-the-loop) tables.
-- create_all won't ALTER existing DBs; run this explicitly on Neon.

CREATE TABLE IF NOT EXISTS wa_conversation (
    id SERIAL PRIMARY KEY,
    customer_phone VARCHAR(20) NOT NULL UNIQUE,
    customer_name VARCHAR(200),
    status VARCHAR(20) DEFAULT 'active',
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wa_conversation_phone ON wa_conversation(customer_phone);

CREATE TABLE IF NOT EXISTS wa_message (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES wa_conversation(id) ON DELETE CASCADE,
    wa_message_id VARCHAR(128) UNIQUE,
    direction VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    message_type VARCHAR(20) DEFAULT 'text',
    media_url VARCHAR(500),
    status VARCHAR(20) DEFAULT 'received',
    drafted_by VARCHAR(20),
    approved_by VARCHAR(255),
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wa_message_conversation ON wa_message(conversation_id);

CREATE TABLE IF NOT EXISTS wa_draft (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES wa_conversation(id) ON DELETE CASCADE,
    trigger_message_id INTEGER REFERENCES wa_message(id) ON DELETE SET NULL,
    draft_text TEXT NOT NULL,
    edited_text TEXT,
    ai_context TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by VARCHAR(255),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_wa_draft_status ON wa_draft(status);
CREATE INDEX IF NOT EXISTS ix_wa_draft_conversation ON wa_draft(conversation_id);
