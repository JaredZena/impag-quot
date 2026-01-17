#!/usr/bin/env python3
"""
Migration script to add topic-based deduplication columns to social_post table:
1. Add topic column (TEXT, NOT NULL after backfill)
2. Add problem_identified column (TEXT, nullable)
3. Add topic_hash column (VARCHAR(64), NOT NULL after backfill, indexed)
4. Create composite indexes for performance
5. Backfill topic from formatted_content.topic or use placeholder

Run this script to apply the migration to your database.

Usage:
    python migrations/add_topic_columns_to_social_post.py
"""

import sys
from pathlib import Path
import hashlib
import json

# Add parent directory to path to import models
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text, create_engine
from config import database_url

# Import topic normalization function
try:
    from routes.social_topic import normalize_topic, compute_topic_hash
except ImportError:
    # Fallback if import fails (for migration script)
    import hashlib
    import re
    
    def normalize_topic(topic: str) -> str:
        """Normalize topic string."""
        if not topic:
            return ""
        normalized = topic.lower()
        normalized = re.sub(r'[-=]+\s*>', '→', normalized)
        normalized = re.sub(r'➜|➡', '→', normalized)
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE
        )
        normalized = emoji_pattern.sub('', normalized)
        normalized = normalized.strip()
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = re.sub(r'\s*→\s*', ' → ', normalized)
        normalized = normalized.strip()
        normalized = re.sub(r'^[^\w→]+|[^\w→]+$', '', normalized)
        return normalized.strip()
    
    def compute_topic_hash(topic: str) -> str:
        """Compute SHA256 hash of normalized topic."""
        normalized = normalize_topic(topic)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def run_migration():
    """Add topic-based deduplication columns to social_post table."""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # Step 0: Check and add prerequisite columns if missing
            print("Step 0: Checking prerequisite columns...")
            
            # Check for external_id (from previous migration)
            check_external_id = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'external_id'
            """)
            result = conn.execute(check_external_id)
            if result.fetchone() is None:
                print("  Adding missing 'external_id' column...")
                conn.execute(text("""
                    ALTER TABLE social_post 
                    ADD COLUMN external_id VARCHAR(255) NULL
                """))
                print("  ✓ Added 'external_id' column")
            else:
                print("  ✓ 'external_id' column exists")
            
            # Check for channel, carousel_slides, needs_music (from previous migration)
            check_channel = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'channel'
            """)
            result = conn.execute(check_channel)
            if result.fetchone() is None:
                print("  Adding missing 'channel' column...")
                conn.execute(text("""
                    ALTER TABLE social_post 
                    ADD COLUMN channel VARCHAR(50) NULL
                """))
                print("  ✓ Added 'channel' column")
            
            check_carousel = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'carousel_slides'
            """)
            result = conn.execute(check_carousel)
            if result.fetchone() is None:
                print("  Adding missing 'carousel_slides' column...")
                conn.execute(text("""
                    ALTER TABLE social_post 
                    ADD COLUMN carousel_slides JSON NULL
                """))
                print("  ✓ Added 'carousel_slides' column")
            
            check_music = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'needs_music'
            """)
            result = conn.execute(check_music)
            if result.fetchone() is None:
                print("  Adding missing 'needs_music' column...")
                conn.execute(text("""
                    ALTER TABLE social_post 
                    ADD COLUMN needs_music BOOLEAN DEFAULT FALSE
                """))
                print("  ✓ Added 'needs_music' column")
            
            # Check for user_feedback (from previous migration)
            check_feedback = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'user_feedback'
            """)
            result = conn.execute(check_feedback)
            if result.fetchone() is None:
                print("  Adding missing 'user_feedback' column...")
                conn.execute(text("""
                    ALTER TABLE social_post 
                    ADD COLUMN user_feedback VARCHAR(20) NULL
                """))
                print("  ✓ Added 'user_feedback' column")
            
            # Step 1: Add topic columns (nullable initially for backfill)
            print("\nStep 1: Adding topic, problem_identified, and topic_hash columns...")
            conn.execute(text("""
                ALTER TABLE social_post 
                ADD COLUMN IF NOT EXISTS topic TEXT,
                ADD COLUMN IF NOT EXISTS problem_identified TEXT,
                ADD COLUMN IF NOT EXISTS topic_hash VARCHAR(64);
            """))
            print("  ✓ Columns added (nullable for backfill)")
            
            # Step 2: Backfill topic from formatted_content.topic or use placeholder
            print("\nStep 2: Backfilling topic column...")
            # First, get all posts without topic
            result = conn.execute(text("""
                SELECT id, formatted_content 
                FROM social_post 
                WHERE topic IS NULL OR topic = ''
            """))
            posts = result.fetchall()
            print(f"  Found {len(posts)} posts to backfill")
            
            placeholder_topic = "sin tema → sin solución"
            placeholder_hash = compute_topic_hash(placeholder_topic)
            
            updated_count = 0
            for post_id, formatted_content in posts:
                topic_to_use = placeholder_topic
                
                # Try to extract topic from formatted_content
                if formatted_content:
                    try:
                        if isinstance(formatted_content, str):
                            fc_dict = json.loads(formatted_content)
                        else:
                            fc_dict = formatted_content
                        
                        if isinstance(fc_dict, dict):
                            # Check for topic in formatted_content
                            extracted_topic = fc_dict.get('topic')
                            if extracted_topic and isinstance(extracted_topic, str) and '→' in extracted_topic:
                                topic_to_use = extracted_topic
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        pass  # Use placeholder if extraction fails
                
                # Normalize and hash
                normalized = normalize_topic(topic_to_use)
                topic_hash = compute_topic_hash(normalized)
                
                # Update row
                conn.execute(text("""
                    UPDATE social_post 
                    SET topic = :topic, topic_hash = :topic_hash
                    WHERE id = :id
                """), {
                    "topic": normalized,
                    "topic_hash": topic_hash,
                    "id": post_id
                })
                updated_count += 1
            
            print(f"  ✓ Backfilled {updated_count} posts")
            
            # Step 3: Make columns NOT NULL
            print("\nStep 3: Making topic and topic_hash NOT NULL...")
            conn.execute(text("""
                ALTER TABLE social_post 
                ALTER COLUMN topic SET NOT NULL,
                ALTER COLUMN topic_hash SET NOT NULL;
            """))
            print("  ✓ Columns set to NOT NULL")
            
            # Step 4: Create index on topic_hash
            print("\nStep 4: Creating index on topic_hash...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_social_post_topic_hash 
                ON social_post(topic_hash);
            """))
            print("  ✓ Index created on topic_hash")
            
            # Step 5: Create composite indexes for performance
            print("\nStep 5: Creating composite indexes...")
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_social_post_topic_hash_date_for 
                ON social_post(topic_hash, date_for);
            """))
            print("  ✓ Composite index (topic_hash, date_for) created")
            
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_social_post_date_for_created_at 
                ON social_post(date_for, created_at);
            """))
            print("  ✓ Composite index (date_for, created_at) created")
            
            # Commit transaction
            trans.commit()
            print("\n✅ Migration completed successfully!")
            print(f"  - Added topic, problem_identified, topic_hash columns")
            print(f"  - Backfilled {updated_count} posts")
            print(f"  - Created indexes for performance")
            
        except Exception as e:
            trans.rollback()
            print(f"\n❌ Migration failed: {e}")
            raise


if __name__ == "__main__":
    run_migration()

