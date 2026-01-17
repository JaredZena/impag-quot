-- Quick fix for missing social_post columns in production
-- SAFE TO RUN - Only adds missing columns and indexes
-- Run this if external_id or other columns are missing

-- CRITICAL: Migrate date_for from VARCHAR to DATE if needed
DO $$
BEGIN
    -- Check current type of date_for column
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' 
        AND column_name = 'date_for'
        AND data_type = 'character varying'
    ) THEN
        -- Convert VARCHAR to DATE
        ALTER TABLE social_post 
        ALTER COLUMN date_for TYPE DATE 
        USING date_for::DATE;
        
        RAISE NOTICE 'Converted date_for from VARCHAR to DATE';
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' 
        AND column_name = 'date_for'
        AND data_type = 'date'
    ) THEN
        RAISE NOTICE 'date_for is already DATE type';
    END IF;
END $$;

-- Add external_id if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'external_id'
    ) THEN
        ALTER TABLE social_post ADD COLUMN external_id VARCHAR(255) NULL;
        CREATE INDEX IF NOT EXISTS idx_social_post_external_id 
            ON social_post(external_id) WHERE external_id IS NOT NULL;
    END IF;
END $$;

-- Add channel if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'channel'
    ) THEN
        ALTER TABLE social_post ADD COLUMN channel VARCHAR(50) NULL;
    END IF;
END $$;

-- Add carousel_slides if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'carousel_slides'
    ) THEN
        ALTER TABLE social_post ADD COLUMN carousel_slides JSON NULL;
    END IF;
END $$;

-- Add needs_music if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'needs_music'
    ) THEN
        ALTER TABLE social_post ADD COLUMN needs_music BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

-- Add user_feedback if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'user_feedback'
    ) THEN
        ALTER TABLE social_post ADD COLUMN user_feedback VARCHAR(20) NULL;
    END IF;
END $$;

-- Add topic columns if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'topic'
    ) THEN
        ALTER TABLE social_post ADD COLUMN topic TEXT;
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'problem_identified'
    ) THEN
        ALTER TABLE social_post ADD COLUMN problem_identified TEXT;
    END IF;
    
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' AND column_name = 'topic_hash'
    ) THEN
        ALTER TABLE social_post ADD COLUMN topic_hash VARCHAR(64);
    END IF;
END $$;

-- Backfill topic columns with placeholder if NULL
-- Note: topic_hash is SHA256 of normalized topic 'sin tema → sin solución'
-- Precomputed hash: caefbb4eb9bcd3e37013189ccdb014934df49bcb9e896f270317eb6874d6f90f
-- (computed in Python as hashlib.sha256('sin tema → sin solución'.encode('utf-8')).hexdigest())

-- First, update any rows with NULL topic or empty topic
UPDATE social_post 
SET 
    topic = 'sin tema → sin solución',
    topic_hash = 'caefbb4eb9bcd3e37013189ccdb014934df49bcb9e896f270317eb6874d6f90f'
WHERE topic IS NULL OR topic = '' OR topic_hash IS NULL OR topic_hash = '';

-- Verify no NULL values remain before setting NOT NULL
-- This will raise an error if there are still NULLs (safety check)
DO $$
DECLARE
    null_topic_count INTEGER;
    null_hash_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_topic_count FROM social_post WHERE topic IS NULL OR topic = '';
    SELECT COUNT(*) INTO null_hash_count FROM social_post WHERE topic_hash IS NULL OR topic_hash = '';
    
    IF null_topic_count > 0 OR null_hash_count > 0 THEN
        RAISE EXCEPTION 'Cannot set NOT NULL: Found % rows with NULL topic and % rows with NULL topic_hash', 
            null_topic_count, null_hash_count;
    END IF;
END $$;

-- Only set NOT NULL if all rows have values (safe)
DO $$
BEGIN
    -- Check if columns are already NOT NULL
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' 
        AND column_name = 'topic' 
        AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE social_post ALTER COLUMN topic SET NOT NULL;
    END IF;
    
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'social_post' 
        AND column_name = 'topic_hash' 
        AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE social_post ALTER COLUMN topic_hash SET NOT NULL;
    END IF;
END $$;

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_social_post_topic_hash 
    ON social_post(topic_hash);

CREATE INDEX IF NOT EXISTS idx_social_post_topic_hash_date_for 
    ON social_post(topic_hash, date_for);

CREATE INDEX IF NOT EXISTS idx_social_post_date_for_created_at 
    ON social_post(date_for, created_at);

