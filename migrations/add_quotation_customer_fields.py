#!/usr/bin/env python3
"""
Migration script to add customer fields to quotation table
Run this script to add the missing columns: customer_name, customer_location, quotation_id
"""

import sys
import os

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from config import database_url

def run_migration():
    """Add customer fields to quotation table"""
    
    # Parse the database URL to get the endpoint ID
    from urllib.parse import urlparse, parse_qs, urlencode
    
    parsed_url = urlparse(database_url)
    endpoint_id = parsed_url.hostname.split('.')[0] if '.' in parsed_url.hostname else None
    
    # Add the endpoint ID to the connection options if available
    if endpoint_id:
        query_params = parse_qs(parsed_url.query)
        query_params['options'] = [f'endpoint={endpoint_id}']
        new_query = urlencode(query_params, doseq=True)
        modified_url = parsed_url._replace(query=new_query).geturl()
        if not modified_url.startswith('postgresql+psycopg2://'):
            modified_url = modified_url.replace('postgresql://', 'postgresql+psycopg2://')
    else:
        modified_url = database_url
        if not modified_url.startswith('postgresql+psycopg2://'):
            modified_url = modified_url.replace('postgresql://', 'postgresql+psycopg2://')
    
    engine = create_engine(modified_url)
    
    print("🔧 Starting migration: Adding customer fields to quotation table...")
    
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        
        try:
            # Add customer_name column
            print("  ➕ Adding customer_name column...")
            conn.execute(text("""
                ALTER TABLE quotation 
                ADD COLUMN IF NOT EXISTS customer_name VARCHAR(200)
            """))
            
            # Add customer_location column
            print("  ➕ Adding customer_location column...")
            conn.execute(text("""
                ALTER TABLE quotation 
                ADD COLUMN IF NOT EXISTS customer_location VARCHAR(200)
            """))
            
            # Add quotation_id column
            print("  ➕ Adding quotation_id column...")
            conn.execute(text("""
                ALTER TABLE quotation 
                ADD COLUMN IF NOT EXISTS quotation_id VARCHAR(50)
            """))
            
            # Commit transaction
            trans.commit()
            print("✅ Migration completed successfully!")
            
            # Verify columns were added
            print("\n📋 Verifying columns...")
            result = conn.execute(text("""
                SELECT 
                    column_name, 
                    data_type, 
                    character_maximum_length,
                    is_nullable
                FROM information_schema.columns
                WHERE table_name = 'quotation' 
                  AND column_name IN ('customer_name', 'customer_location', 'quotation_id')
                ORDER BY column_name
            """))
            
            rows = result.fetchall()
            if rows:
                print("\n✅ Columns verified:")
                for row in rows:
                    print(f"   - {row[0]}: {row[1]}({row[2] or 'N/A'}), nullable={row[3]}")
            else:
                print("⚠️  Warning: Could not verify columns (they may have already existed)")
                
        except Exception as e:
            trans.rollback()
            print(f"❌ Migration failed: {e}")
            raise
    
    print("\n✨ Migration script completed!")

if __name__ == "__main__":
    run_migration()



