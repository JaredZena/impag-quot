#!/usr/bin/env python3
"""
Script to restore stock data from CSV backup to Product table (PostgreSQL version).

This script will:
1. Read the CSV backup file
2. Connect to PostgreSQL database
3. Match products by ID or name
4. Restore the stock column values
5. Handle any mismatches or missing data gracefully

Usage:
python migrations/restore_stock_from_csv_postgres.py path/to/your/backup.csv
"""

import os
import sys
import csv
import psycopg2
from psycopg2.extras import RealDictCursor
from decimal import Decimal
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_database_url():
    """Get database URL from environment or use default."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ Error: DATABASE_URL environment variable not set")
        print("Please set your DATABASE_URL environment variable with your PostgreSQL connection string")
        print("Example: export DATABASE_URL='postgresql://user:password@host:port/database'")
        sys.exit(1)
    return database_url

def restore_stock_from_csv(csv_file_path):
    """Restore stock data from CSV backup to Product table."""
    
    if not os.path.exists(csv_file_path):
        print(f"❌ Error: CSV file not found: {csv_file_path}")
        return False
    
    # Connect to PostgreSQL database
    database_url = get_database_url()
    
    try:
        print("🚀 Starting stock restoration from CSV...")
        print(f"📁 CSV file: {csv_file_path}")
        print(f"🗄️  Database: PostgreSQL (Neon)")
        
        # Connect to database with SSL
        conn = psycopg2.connect(database_url, sslmode='require')
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # First, let's check if the stock column exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'product' AND column_name = 'stock'
        """)
        stock_column_exists = cursor.fetchone() is not None
        
        if not stock_column_exists:
            print("⚠️  Stock column doesn't exist. Adding it...")
            cursor.execute("ALTER TABLE product ADD COLUMN stock INTEGER DEFAULT 0")
            print("✅ Stock column added to product table")
        
        # Read CSV file
        print(f"📖 Reading CSV file...")
        restored_count = 0
        skipped_count = 0
        not_found_count = 0
        
        with open(csv_file_path, 'r', encoding='utf-8') as csvfile:
            # Try to detect the delimiter
            sample = csvfile.read(1024)
            csvfile.seek(0)
            sniffer = csv.Sniffer()
            delimiter = sniffer.sniff(sample).delimiter
            
            reader = csv.DictReader(csvfile, delimiter=delimiter)
            
            print(f"📊 CSV columns found: {reader.fieldnames}")
            
            # Check if we have the required columns
            if 'id' not in reader.fieldnames and 'name' not in reader.fieldnames:
                print("❌ Error: CSV must contain either 'id' or 'name' column")
                return False
            
            if 'stock' not in reader.fieldnames:
                print("❌ Error: CSV must contain 'stock' column")
                return False
            
            for row_num, row in enumerate(reader, 1):
                try:
                    # Get product identifier
                    product_id = row.get('id')
                    product_name = row.get('name', '').strip()
                    stock_value = row.get('stock', '0').strip()
                    
                    # Skip if no stock value or stock is 0
                    if not stock_value or stock_value == '0' or stock_value == '':
                        continue
                    
                    try:
                        stock_amount = int(float(stock_value))
                    except (ValueError, TypeError):
                        print(f"⚠️  Row {row_num}: Invalid stock value '{stock_value}' for product '{product_name}' - skipping")
                        skipped_count += 1
                        continue
                    
                    # Try to find the product by ID first, then by name
                    product_found = False
                    
                    if product_id:
                        try:
                            product_id_int = int(product_id)
                            cursor.execute("SELECT id, name FROM product WHERE id = %s", (product_id_int,))
                            result = cursor.fetchone()
                            if result:
                                cursor.execute("UPDATE product SET stock = %s WHERE id = %s", (stock_amount, product_id_int))
                                print(f"✅ Restored {stock_amount} units to Product ID {product_id_int}: {result['name']}")
                                restored_count += 1
                                product_found = True
                        except ValueError:
                            pass  # product_id is not a valid integer
                    
                    # If not found by ID, try by name
                    if not product_found and product_name:
                        cursor.execute("SELECT id, name FROM product WHERE name = %s", (product_name,))
                        result = cursor.fetchone()
                        if result:
                            cursor.execute("UPDATE product SET stock = %s WHERE id = %s", (stock_amount, result['id']))
                            print(f"✅ Restored {stock_amount} units to Product ID {result['id']}: {result['name']}")
                            restored_count += 1
                            product_found = True
                    
                    if not product_found:
                        print(f"⚠️  Row {row_num}: Product not found - ID: {product_id}, Name: '{product_name}'")
                        not_found_count += 1
                        
                except Exception as e:
                    print(f"❌ Error processing row {row_num}: {e}")
                    skipped_count += 1
                    continue
        
        # Commit changes
        conn.commit()
        
        print(f"\n🎉 Stock restoration completed!")
        print(f"📊 Summary:")
        print(f"   • Products restored: {restored_count}")
        print(f"   • Products not found: {not_found_count}")
        print(f"   • Rows skipped: {skipped_count}")
        
        # Verify restoration
        cursor.execute("SELECT COUNT(*) FROM product WHERE stock > 0")
        products_with_stock = cursor.fetchone()[0]
        print(f"   • Products with stock after restoration: {products_with_stock}")
        
        if restored_count > 0:
            print(f"\n✅ Stock data successfully restored!")
        else:
            print(f"\n⚠️  No stock data was restored. Check the CSV format and product matching.")
        
        return True
        
    except psycopg2.Error as e:
        print(f"❌ Database error during restoration: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during restoration: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def main():
    if len(sys.argv) != 2:
        print("Usage: python restore_stock_from_csv_postgres.py <csv_file_path>")
        print("Example: python restore_stock_from_csv_postgres.py productsoct262025.csv")
        sys.exit(1)
    
    csv_file_path = sys.argv[1]
    
    print("=" * 60)
    print("📦 STOCK RESTORATION SCRIPT (PostgreSQL)")
    print("=" * 60)
    print("This script will restore stock data from CSV backup to Product table.")
    print("=" * 60)
    
    # Ask for confirmation
    response = input(f"\nDo you want to restore stock from '{csv_file_path}'? (y/N): ").strip().lower()
    if response not in ['y', 'yes']:
        print("❌ Restoration cancelled.")
        sys.exit(0)
    
    success = restore_stock_from_csv(csv_file_path)
    
    if success:
        print(f"\n✅ Restoration completed successfully!")
    else:
        print(f"\n❌ Restoration failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
