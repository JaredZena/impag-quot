-- Migration: Add user_feedback column to social_post table
-- Date: 2025-01-XX
-- Description: Adds a column to store user feedback (like/dislike) for social posts

-- Add the user_feedback column
ALTER TABLE social_post 
ADD COLUMN IF NOT EXISTS user_feedback VARCHAR(20) NULL;

-- Add a comment to document the column
COMMENT ON COLUMN social_post.user_feedback IS 'User feedback: ''like'', ''dislike'', or NULL';

-- Optional: Create an index if you plan to query by feedback frequently
-- CREATE INDEX IF NOT EXISTS idx_social_post_user_feedback ON social_post(user_feedback);




