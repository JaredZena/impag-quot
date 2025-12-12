-- Add channel-specific columns to social_post table
-- Run this migration to add the new fields

ALTER TABLE social_post 
ADD COLUMN IF NOT EXISTS channel VARCHAR(50),
ADD COLUMN IF NOT EXISTS carousel_slides JSON,
ADD COLUMN IF NOT EXISTS needs_music BOOLEAN DEFAULT FALSE;

-- Add comments for documentation
COMMENT ON COLUMN social_post.channel IS 'Selected channel: wa-status, fb-post, fb-reel, tiktok, etc.';
COMMENT ON COLUMN social_post.carousel_slides IS 'Array of image prompts for carousels (TikTok 2-3, FB/IG up to 10)';
COMMENT ON COLUMN social_post.needs_music IS 'Whether this content needs background music';
