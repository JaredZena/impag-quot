#!/usr/bin/env python3
"""
Migration script to add production-hardening features to social_post table:
1. Add external_id column (indexed) for efficient lookups
2. Change date_for from VARCHAR to DATE type
3. Add composite indexes for performance
4. Add index on formatted_content JSON field (PostgreSQL JSONB expression)

Run this script to apply the migration to your database.

Usage:
    python migrations/add_social_post_hardening.py
"""

import sys
from pathlib import Path

# Add parent directory to path to import models
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text, create_engine
from config import database_url

def run_migration():
    """Add production-hardening features to social_post table."""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. Add external_id column if it doesn't exist
            check_external_id = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'external_id'
            """)
            result = conn.execute(check_external_id)
            exists = result.fetchone() is not None
            
            if not exists:
                print("Adding 'external_id' column to 'social_post' table...")
                alter_query = text("""
                    ALTER TABLE social_post 
                    ADD COLUMN external_id VARCHAR(255) NULL
                """)
                conn.execute(alter_query)
                print("✓ Added 'external_id' column")
            else:
                print("✓ Column 'external_id' already exists")
            
            # 2. Create index on external_id
            check_index = text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'social_post' 
                AND indexname = 'idx_social_post_external_id'
            """)
            result = conn.execute(check_index)
            exists = result.fetchone() is not None
            
            if not exists:
                print("Creating index on 'external_id'...")
                index_query = text("""
                    CREATE INDEX idx_social_post_external_id 
                    ON social_post(external_id) 
                    WHERE external_id IS NOT NULL
                """)
                conn.execute(index_query)
                print("✓ Created index on 'external_id'")
            else:
                print("✓ Index on 'external_id' already exists")
            
            # 3. Migrate date_for from VARCHAR to DATE
            # First check current type
            check_date_type = text("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'social_post' 
                AND column_name = 'date_for'
            """)
            result = conn.execute(check_date_type)
            row = result.fetchone()
            
            if row and row[0] == 'character varying':
                print("Migrating 'date_for' from VARCHAR to DATE...")
                # PostgreSQL: Use ALTER COLUMN with USING clause
                migrate_date_query = text("""
                    ALTER TABLE social_post 
                    ALTER COLUMN date_for TYPE DATE 
                    USING date_for::DATE
                """)
                conn.execute(migrate_date_query)
                print("✓ Migrated 'date_for' to DATE type")
            elif row and row[0] == 'date':
                print("✓ 'date_for' is already DATE type")
            else:
                print(f"⚠️  Warning: 'date_for' has unexpected type: {row[0] if row else 'unknown'}")
            
            # 4. Create composite index on (date_for, created_at) for common queries
            check_composite_index = text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'social_post' 
                AND indexname = 'idx_social_post_date_created'
            """)
            result = conn.execute(check_composite_index)
            exists = result.fetchone() is not None
            
            if not exists:
                print("Creating composite index on (date_for, created_at)...")
                composite_index_query = text("""
                    CREATE INDEX idx_social_post_date_created 
                    ON social_post(date_for DESC, created_at DESC)
                """)
                conn.execute(composite_index_query)
                print("✓ Created composite index on (date_for, created_at)")
            else:
                print("✓ Composite index on (date_for, created_at) already exists")
            
            # 5. Create index on formatted_content->>'id' for JSON lookups (PostgreSQL)
            # This allows efficient queries like: WHERE formatted_content->>'id' = 'some-id'
            check_json_index = text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'social_post' 
                AND indexname = 'idx_social_post_formatted_content_id'
            """)
            result = conn.execute(check_json_index)
            exists = result.fetchone() is not None
            
            if not exists:
                print("Creating index on formatted_content->>'id'...")
                # First, we need to ensure formatted_content is JSONB (not JSON)
                # Check current type
                check_json_type = text("""
                    SELECT data_type 
                    FROM information_schema.columns 
                    WHERE table_name = 'social_post' 
                    AND column_name = 'formatted_content'
                """)
                result = conn.execute(check_json_type)
                row = result.fetchone()
                
                if row and row[0] == 'json':
                    # Convert JSON to JSONB for better performance and indexing
                    print("  Converting formatted_content from JSON to JSONB...")
                    convert_jsonb_query = text("""
                        ALTER TABLE social_post 
                        ALTER COLUMN formatted_content TYPE JSONB 
                        USING formatted_content::JSONB
                    """)
                    conn.execute(convert_jsonb_query)
                    print("  ✓ Converted formatted_content to JSONB")
                elif row and row[0] == 'jsonb':
                    print("  ✓ formatted_content is already JSONB")
                
                # Create GIN index on JSONB for efficient lookups
                json_index_query = text("""
                    CREATE INDEX idx_social_post_formatted_content_id 
                    ON social_post USING GIN ((formatted_content->>'id'))
                """)
                conn.execute(json_index_query)
                print("✓ Created index on formatted_content->>'id'")
            else:
                print("✓ Index on formatted_content->>'id' already exists")
            
            # 6. Add indexes for SupplierProduct queries (is_active, archived_at, category_id)
            check_supplier_product_indexes = text("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'supplier_product' 
                AND indexname IN (
                    'idx_supplier_product_active_archived',
                    'idx_supplier_product_category_active'
                )
            """)
            result = conn.execute(check_supplier_product_indexes)
            existing_indexes = {row[0] for row in result.fetchall()}
            
            if 'idx_supplier_product_active_archived' not in existing_indexes:
                print("Creating index on supplier_product(is_active, archived_at)...")
                supplier_index_query = text("""
                    CREATE INDEX idx_supplier_product_active_archived 
                    ON supplier_product(is_active, archived_at) 
                    WHERE is_active = TRUE AND archived_at IS NULL
                """)
                conn.execute(supplier_index_query)
                print("✓ Created index on supplier_product(is_active, archived_at)")
            else:
                print("✓ Index on supplier_product(is_active, archived_at) already exists")
            
            if 'idx_supplier_product_category_active' not in existing_indexes:
                print("Creating index on supplier_product(category_id, is_active)...")
                category_index_query = text("""
                    CREATE INDEX idx_supplier_product_category_active 
                    ON supplier_product(category_id, is_active) 
                    WHERE is_active = TRUE AND archived_at IS NULL
                """)
                conn.execute(category_index_query)
                print("✓ Created index on supplier_product(category_id, is_active)")
            else:
                print("✓ Index on supplier_product(category_id, is_active) already exists")
            
            trans.commit()
            print("\n✅ Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            print(f"\n❌ Migration failed: {e}")
            raise

if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"Error running migration: {e}")
        sys.exit(1)


