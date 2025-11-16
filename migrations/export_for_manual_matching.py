#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export Products and Suppliers for Manual Matching

Generates two CSV files:
1. products_for_matching.csv - Products that need suppliers
2. suppliers_list.csv - All suppliers from database
"""

import os
import sys
import csv
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import Supplier, SessionLocal
from sqlalchemy.orm import Session

def export_products():
    """Export products from the products_needing_suppliers.csv file."""
    csv_path = os.path.join(
        os.path.dirname(__file__),
        'products_needing_suppliers.csv'
    )
    
    if not os.path.exists(csv_path):
        print(f"❌ Error: CSV file not found: {csv_path}")
        return False
    
    output_path = os.path.join(
        os.path.dirname(__file__),
        'products_for_matching.csv'
    )
    
    # Read and write with cleaner format
    with open(csv_path, 'r', encoding='utf-8') as f_in:
        reader = csv.DictReader(f_in)
        products = list(reader)
    
    # Write to output file
    with open(output_path, 'w', encoding='utf-8', newline='') as f_out:
        fieldnames = ['product_id', 'product_name', 'sku', 'stock', 'price', 'category_name', 'extracted_supplier']
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        
        for product in products:
            # Extract supplier from name if present
            product_name = product.get('product_name', '')
            extracted_supplier = ''
            if '(' in product_name and ')' in product_name:
                # Extract text in parentheses
                start = product_name.rfind('(')
                end = product_name.rfind(')')
                if start != -1 and end != -1 and end > start:
                    extracted_supplier = product_name[start+1:end].strip()
            
            writer.writerow({
                'product_id': product.get('product_id', ''),
                'product_name': product.get('product_name', ''),
                'sku': product.get('sku', ''),
                'stock': product.get('stock', '0'),
                'price': product.get('price', '0'),
                'category_name': product.get('category_name', ''),
                'extracted_supplier': extracted_supplier
            })
    
    print(f"✅ Exported {len(products)} products to: {output_path}")
    return True

def export_suppliers(session: Session):
    """Export all suppliers from database."""
    suppliers = session.query(Supplier).filter(
        Supplier.archived_at.is_(None)
    ).order_by(Supplier.name).all()
    
    output_path = os.path.join(
        os.path.dirname(__file__),
        'suppliers_list.csv'
    )
    
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        fieldnames = ['supplier_id', 'name', 'common_name', 'legal_name']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for supplier in suppliers:
            writer.writerow({
                'supplier_id': supplier.id,
                'name': supplier.name or '',
                'common_name': supplier.common_name or '',
                'legal_name': supplier.legal_name or ''
            })
    
    print(f"✅ Exported {len(suppliers)} suppliers to: {output_path}")
    return True

def main():
    """Main function."""
    print("=" * 80)
    print("📋 EXPORT FOR MANUAL MATCHING")
    print("=" * 80)
    
    # Export products
    print("\n📦 Exporting products...")
    export_products()
    
    # Export suppliers
    print("\n🏢 Exporting suppliers...")
    session = SessionLocal()
    try:
        export_suppliers(session)
    finally:
        session.close()
    
    print("\n" + "=" * 80)
    print("✅ Export complete!")
    print("=" * 80)
    print("\nFiles created:")
    print("  1. products_for_matching.csv - Products that need suppliers")
    print("  2. suppliers_list.csv - All suppliers from database")
    print("\nYou can now manually match products to suppliers using these files.")

if __name__ == "__main__":
    main()

