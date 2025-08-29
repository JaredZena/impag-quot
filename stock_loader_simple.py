#!/usr/bin/env python3
"""
Simple Stock Loader Script for IMPAG
Loads stock data from CSV/Google Sheets export into the database
"""

import csv
import sys
import re
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from models import SessionLocal, Product, ProductUnit

def parse_currency(value_str):
    """Parse currency string like '$123.45' to Decimal"""
    if not value_str or value_str.strip() == '':
        return None
    
    # Remove currency symbol and commas
    cleaned = re.sub(r'[\$,]', '', value_str.strip())
    
    try:
        return Decimal(cleaned)
    except:
        return None

def parse_float(value_str):
    """Parse float string to float, handling empty values"""
    if not value_str or value_str.strip() == '':
        return None
    
    try:
        return float(value_str.strip())
    except:
        return None

def normalize_unit(unit_str):
    """Normalize unit string to ProductUnit enum"""
    if not unit_str:
        return ProductUnit.PIEZA
    
    unit_upper = unit_str.upper().strip()
    
    # Map various unit formats to our enum
    unit_mapping = {
        'PIEZA': ProductUnit.PIEZA,
        'PIEZAS': ProductUnit.PIEZA,
        'PZ': ProductUnit.PIEZA,
        'ROLLO': ProductUnit.ROLLO,
        'ROLLOS': ProductUnit.ROLLO,
        'METRO': ProductUnit.METRO,
        'METROS': ProductUnit.METRO,
        'M': ProductUnit.METRO,
        'KG': ProductUnit.KG,
        'KILO': ProductUnit.KG,
        'KILOS': ProductUnit.KG,
        'PAQUETE': ProductUnit.PAQUETE,
        'PAQUETES': ProductUnit.PAQUETE,
        'PKG': ProductUnit.PAQUETE,
        'PACK': ProductUnit.PAQUETE,
        'KIT': ProductUnit.KIT,
        'KITS': ProductUnit.KIT,
        'BULTO': ProductUnit.PIEZA,   # Treat bultos as pieces
    }
    
    return unit_mapping.get(unit_upper, ProductUnit.PIEZA)

def generate_sku(product_name, existing_skus):
    """Generate a unique SKU from product name"""
    # Clean product name and create base SKU
    base_sku = re.sub(r'[^\w\s-]', '', product_name.upper())
    base_sku = re.sub(r'\s+', '-', base_sku.strip())
    
    # Limit length
    if len(base_sku) > 50:
        base_sku = base_sku[:50]
    
    # Ensure uniqueness
    sku = base_sku
    counter = 1
    while sku in existing_skus:
        sku = f"{base_sku}-{counter}"
        counter += 1
    
    existing_skus.add(sku)
    return sku

def load_stock_from_csv_simple(csv_file_path):
    """Load stock data from CSV file - simple version"""
    db = SessionLocal()
    
    try:
        # Get existing products and SKUs
        existing_products = {p.name.strip().upper(): p for p in db.query(Product).all()}
        existing_skus = {p.sku for p in existing_products.values()}
        
        created_count = 0
        updated_count = 0
        error_count = 0
        
        with open(csv_file_path, 'r', encoding='utf-8') as file:
            csv_reader = csv.reader(file)
            
            # Get the first row to check if it's header
            first_row = next(csv_reader)
            row_num = 1
            
            # Check if first row looks like header
            if 'Material' in first_row[0] or 'Producto' in first_row[0]:
                print("Detected header row, skipping...")
                row_num = 2
            else:
                # Process first row as data
                try:
                    process_row(first_row, row_num, db, existing_products, existing_skus, created_count, updated_count, error_count)
                except Exception as e:
                    print(f"Row {row_num}: Error - {str(e)}")
                    error_count += 1
                row_num += 1
            
            # Process remaining rows
            for row in csv_reader:
                try:
                    result = process_row(row, row_num, db, existing_products, existing_skus)
                    if result == 'created':
                        created_count += 1
                    elif result == 'updated':
                        updated_count += 1
                    elif result == 'error':
                        error_count += 1
                except Exception as e:
                    print(f"Row {row_num}: Error - {str(e)}")
                    error_count += 1
                
                row_num += 1
        
        print(f"\n=== IMPORT SUMMARY ===")
        print(f"Products created: {created_count}")
        print(f"Products updated: {updated_count}")
        print(f"Errors: {error_count}")
        print(f"Total processed: {created_count + updated_count}")
        
    except Exception as e:
        print(f"Database error: {str(e)}")
        raise
    finally:
        db.close()

def process_row(row, row_num, db, existing_products, existing_skus):
    """Process a single row"""
    if len(row) < 4:
        print(f"Row {row_num}: Skipping incomplete row")
        return 'skipped'
    
    # Parse CSV columns - flexible parsing
    product_name = row[0].strip()
    unit_str = row[1].strip() if len(row) > 1 else 'PIEZA'
    stock = parse_float(row[3]) if len(row) > 3 else 0  # Column 3 is "Cantidad en Stock"
    unit_cost = parse_currency(row[5]) if len(row) > 5 else None  # Column 5 is "Costo Unitario"
    
    if not product_name:
        print(f"Row {row_num}: Skipping row with empty product name")
        return 'skipped'
    
    # Convert stock to integer
    stock_int = int(stock) if stock is not None else 0
    
    # Normalize unit
    unit = normalize_unit(unit_str)
    
    # Check if product exists
    product_key = product_name.strip().upper()
    existing_product = existing_products.get(product_key)
    
    if existing_product:
        # Update existing product
        existing_product.stock = stock_int
        if unit_cost:
            existing_product.price = unit_cost
        existing_product.last_updated = datetime.now()
        
        # Commit individual update
        try:
            db.commit()
            print(f"Row {row_num}: Updated {product_name} - Stock: {stock_int}, Price: {unit_cost}")
            return 'updated'
        except Exception as e:
            db.rollback()
            print(f"Row {row_num}: Error updating {product_name} - {str(e)}")
            return 'error'
    else:
        # Create new product
        sku = generate_sku(product_name, existing_skus)
        
        new_product = Product(
            name=product_name,
            sku=sku,
            unit=unit,
            stock=stock_int,
            price=unit_cost,
            is_active=True,
            last_updated=datetime.now()
        )
        
        db.add(new_product)
        
        # Commit individual insert
        try:
            db.commit()
            db.refresh(new_product)
            existing_products[product_key] = new_product
            print(f"Row {row_num}: Created {product_name} - SKU: {sku}, Stock: {stock_int}, Price: {unit_cost}")
            return 'created'
        except Exception as e:
            db.rollback()
            print(f"Row {row_num}: Error creating {product_name} - {str(e)}")
            return 'error'

def main():
    if len(sys.argv) != 2:
        print("Usage: python stock_loader_simple.py <csv_file_path>")
        print("\nExample: python stock_loader_simple.py stock_data.csv")
        sys.exit(1)
    
    csv_file_path = sys.argv[1]
    
    try:
        load_stock_from_csv_simple(csv_file_path)
        print("Stock data loaded successfully!")
    except FileNotFoundError:
        print(f"Error: File '{csv_file_path}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
