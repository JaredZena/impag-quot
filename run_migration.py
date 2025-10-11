#!/usr/bin/env python3
"""
Run the currency support migration
"""

import sqlite3
import os

def run_migration():
    # Read the migration file
    with open('migrations/add_currency_support.sql', 'r') as f:
        migration_sql = f.read()
    
    # Connect to database (you may need to adjust the database path)
    # Check if you have a main database file
    db_files = [f for f in os.listdir('.') if f.endswith('.db')]
    print(f"Found database files: {db_files}")
    
    # You'll need to specify your main database file
    # For example: "main.db" or "impag.db" or whatever your main database is
    db_path = input("Enter your main database file name (e.g., main.db): ")
    
    if not os.path.exists(db_path):
        print(f"Database file {db_path} not found!")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Split the migration into individual statements
        statements = migration_sql.split(';')
        
        for statement in statements:
            statement = statement.strip()
            if statement:
                print(f"Executing: {statement[:50]}...")
                cursor.execute(statement)
        
        conn.commit()
        print("✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    run_migration()
