#!/usr/bin/env python3
"""
Fix enum issue and load stock data
"""

import sys
import os
import csv
import re
from decimal import Decimal
from datetime import datetime

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from models import SessionLocal, Product, ProductUnit
    from sqlalchemy.orm import Session
    from sqlalchemy import text
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure you're in the virtual environment with: source venv/bin/activate")
    sys.exit(1)

def fix_database_and_load(csv_file_path):
    """Fix database enum issue and load data"""
    db = SessionLocal()
    
    try:
        # First, let's try to create a few test products manually
        print("Testing database connection...")
        
        # Check if we can query existing products
        existing_count = db.query(Product).count()
        print(f"Found {existing_count} existing products in database")
        
        # Try to create a simple test product
        test_product = Product(
            name="TEST PRODUCT",
            sku="TEST-PRODUCT-1",
            unit=ProductUnit.PIEZA,
            stock=10,
            price=Decimal('1.00'),
            is_active=True
        )
        
        db.add(test_product)
        db.commit()
        print("✅ Test product created successfully!")
        
        # Delete the test product
        db.delete(test_product)
        db.commit()
        print("✅ Test product deleted successfully!")
        
        # Now load real data
        print("Loading CSV data...")
        load_csv_data(db, csv_file_path)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()

def load_csv_data(db, csv_file_path):
    """Load data from CSV"""
    created_count = 0
    error_count = 0
    
    with open(csv_file_path, 'r', encoding='utf-8') as file:
        csv_reader = csv.reader(file)
        
        # Skip header
        next(csv_reader)
        
        for row_num, row in enumerate(csv_reader, start=2):
            if len(row) < 6:
                continue
                
            product_name = row[0].strip()
            if not product_name or product_name == 'Material':
                continue
                
            unit_str = row[1].strip() if row[1] else 'PIEZA'
            stock_str = row[3].strip() if len(row) > 3 else '0'
            price_str = row[5].strip() if len(row) > 5 else ''
            
            try:
                # Parse stock
                stock = int(float(stock_str)) if stock_str and stock_str != '' else 0
                
                # Parse price
                price = None
                if price_str:
                    # Remove $ and commas
                    cleaned_price = re.sub(r'[\$,]', '', price_str)
                    if cleaned_price:
                        price = Decimal(cleaned_price)
                
                # Map unit
                unit = ProductUnit.PIEZA  # Default
                if unit_str.upper() in ['ROLLO', 'ROLLOS']:
                    unit = ProductUnit.ROLLO
                elif unit_str.upper() in ['METRO', 'METROS', 'M']:
                    unit = ProductUnit.METRO
                elif unit_str.upper() in ['KG', 'KILO', 'KILOS']:
                    unit = ProductUnit.KG
                elif unit_str.upper() in ['PAQUETE', 'PAQUETES', 'PKG', 'PACK']:
                    unit = ProductUnit.PAQUETE
                elif unit_str.upper() in ['KIT', 'KITS']:
                    unit = ProductUnit.KIT
                
                # Generate SKU
                sku = re.sub(r'[^\w\s-]', '', product_name.upper())
                sku = re.sub(r'\s+', '-', sku.strip())[:50]
                
                # Make SKU unique
                base_sku = sku
                counter = 1
                existing = db.query(Product).filter(Product.sku == sku).first()
                while existing:
                    sku = f"{base_sku}-{counter}"
                    counter += 1
                    existing = db.query(Product).filter(Product.sku == sku).first()
                
                # Create product
                product = Product(
                    name=product_name,
                    sku=sku,
                    unit=unit,
                    stock=stock,
                    price=price,
                    is_active=True
                )
                
                db.add(product)
                db.commit()
                
                print(f"Row {row_num}: Created {product_name} (Stock: {stock}, Price: {price})")
                created_count += 1
                
                # Limit batch processing to avoid overwhelming output
                if created_count % 50 == 0:
                    print(f"Processed {created_count} products...")
                    
            except Exception as e:
                db.rollback()
                print(f"Row {row_num}: Error with {product_name} - {str(e)}")
                error_count += 1
                continue
    
    print(f"\n=== RESULTS ===")
    print(f"Created: {created_count}")
    print(f"Errors: {error_count}")

def main():
    if len(sys.argv) != 2:
        print("Usage: python fix_enum_and_load.py <csv_file_path>")
        sys.exit(1)
    
    csv_file_path = sys.argv[1]
    
    if not os.path.exists(csv_file_path):
        print(f"Error: File '{csv_file_path}' not found.")
        sys.exit(1)
    
    try:
        fix_database_and_load(csv_file_path)
    except Exception as e:
        print(f"Failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
