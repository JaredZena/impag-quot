#!/usr/bin/env python3
"""
Script to restore stock data from CSV backup to Product table.

This script will:
1. Read the CSV backup file
2. Match products by ID or name
3. Restore the stock column values
4. Handle any mismatches or missing data gracefully

Usage:
python migrations/restore_stock_from_csv.py path/to/your/backup.csv
"""

import os
import sys
import csv
import sqlite3
from decimal import Decimal

def get_database_url():
    """Get database URL from environment or use default."""
    return os.getenv("DATABASE_URL", "sqlite:///./impag.db")

def restore_stock_from_csv(csv_file_path):
    """Restore stock data from CSV backup to Product table."""
    
    if not os.path.exists(csv_file_path):
        print(f"❌ Error: CSV file not found: {csv_file_path}")
        return False
    
    # Connect to SQLite database
    db_path = get_database_url().replace("sqlite:///", "")
    if not os.path.exists(db_path):
        print(f"❌ Error: Database not found: {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        print("🚀 Starting stock restoration from CSV...")
        print(f"📁 CSV file: {csv_file_path}")
        print(f"🗄️  Database: {db_path}")
        
        # First, let's check if the stock column exists
        cursor.execute("PRAGMA table_info(product)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'stock' not in columns:
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
                            cursor.execute("SELECT id, name FROM product WHERE id = ?", (product_id_int,))
                            result = cursor.fetchone()
                            if result:
                                cursor.execute("UPDATE product SET stock = ? WHERE id = ?", (stock_amount, product_id_int))
                                print(f"✅ Restored {stock_amount} units to Product ID {product_id_int}: {result[1]}")
                                restored_count += 1
                                product_found = True
                        except ValueError:
                            pass  # product_id is not a valid integer
                    
                    # If not found by ID, try by name
                    if not product_found and product_name:
                        cursor.execute("SELECT id, name FROM product WHERE name = ?", (product_name,))
                        result = cursor.fetchone()
                        if result:
                            cursor.execute("UPDATE product SET stock = ? WHERE id = ?", (stock_amount, result[0]))
                            print(f"✅ Restored {stock_amount} units to Product ID {result[0]}: {result[1]}")
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
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error during restoration: {e}")
        return False
    finally:
        conn.close()

def main():
    if len(sys.argv) != 2:
        print("Usage: python restore_stock_from_csv.py <csv_file_path>")
        print("Example: python restore_stock_from_csv.py productsoct262025.csv")
        sys.exit(1)
    
    csv_file_path = sys.argv[1]
    
    print("=" * 60)
    print("📦 STOCK RESTORATION SCRIPT")
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




