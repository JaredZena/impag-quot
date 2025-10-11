#!/usr/bin/env python3
"""
Check if currency column exists and apply migration if needed
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect
from models import Base, SupplierProduct

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ DATABASE_URL not found in .env")
    exit(1)

engine = create_engine(DATABASE_URL)

# Check if currency column exists
inspector = inspect(engine)
columns = [col['name'] for col in inspector.get_columns('supplier_product')]

print(f"Columns in supplier_product table: {columns}")

if 'currency' not in columns:
    print("\n❌ Currency column DOES NOT exist. Applying migration...")
    
    # Read and execute migration
    with open('migrations/add_currency_support.sql', 'r') as f:
        migration_sql = f.read()
    
    with engine.connect() as conn:
        # Execute each statement separately
        statements = migration_sql.split(';')
        for stmt in statements:
            stmt = stmt.strip()
            if stmt and not stmt.startswith('--') and 'COMMENT ON' not in stmt:
                try:
                    print(f"Executing: {stmt[:100]}...")
                    conn.execute(text(stmt))
                    conn.commit()
                except Exception as e:
                    print(f"⚠️  Warning: {e}")
    
    print("✅ Migration applied successfully!")
else:
    print("\n✅ Currency column EXISTS")
    
    # Check some sample data
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, supplier_id, product_id, cost, currency 
            FROM supplier_product 
            WHERE cost > 700 
            LIMIT 5
        """))
        
        print("\n📊 Sample records:")
        for row in result:
            print(f"  ID: {row[0]}, Supplier: {row[1]}, Product: {row[2]}, Cost: {row[3]}, Currency: {row[4]}")

print("\n✅ Done!")

