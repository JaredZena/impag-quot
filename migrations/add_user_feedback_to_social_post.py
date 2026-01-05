#!/usr/bin/env python3
"""
Migration script to add user_feedback column to social_post table.
Run this script to apply the migration to your database.

Usage:
    python migrations/add_user_feedback_to_social_post.py
"""

import sys
from pathlib import Path

# Add parent directory to path to import models
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from models import get_db, Base
from config import database_url
from sqlalchemy import create_engine

def run_migration():
    """Add user_feedback column to social_post table."""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Check if column already exists
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'social_post' 
            AND column_name = 'user_feedback'
        """)
        result = conn.execute(check_query)
        exists = result.fetchone() is not None
        
        if exists:
            print("✓ Column 'user_feedback' already exists in 'social_post' table.")
            return
        
        # Add the column
        print("Adding 'user_feedback' column to 'social_post' table...")
        alter_query = text("""
            ALTER TABLE social_post 
            ADD COLUMN user_feedback VARCHAR(20) NULL
        """)
        conn.execute(alter_query)
        conn.commit()
        
        print("✓ Successfully added 'user_feedback' column to 'social_post' table.")
        print("  Column accepts values: 'like', 'dislike', or NULL")

if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        sys.exit(1)




