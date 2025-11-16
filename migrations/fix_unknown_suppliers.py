#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fix Unknown Supplier Assignments

This script finds products assigned to "Unknown Supplier" and attempts to
match them to real suppliers based on the supplier_name in the CSV.
"""

import os
import sys
import csv
from typing import Optional, Dict
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import Supplier, SupplierProduct, SessionLocal
from sqlalchemy.orm import Session

def find_supplier_by_name(session: Session, supplier_name: str) -> Optional[Supplier]:
    """
    Find a supplier by name (case-insensitive, fuzzy matching).
    
    Args:
        session: Database session
        supplier_name: Supplier name to search for
        
    Returns:
        Supplier object if found, None otherwise
    """
    if not supplier_name or supplier_name.lower() in ['no supplier match found', 'unknown supplier', '']:
        return None
    
    # Try exact match first (case-insensitive)
    supplier = session.query(Supplier).filter(
        Supplier.name.ilike(supplier_name),
        Supplier.archived_at.is_(None)
    ).first()
    
    if supplier:
        return supplier
    
    # Try common_name match
    supplier = session.query(Supplier).filter(
        Supplier.common_name.ilike(supplier_name),
        Supplier.archived_at.is_(None)
    ).first()
    
    if supplier:
        return supplier
    
    # Try partial match (contains) in name
    supplier = session.query(Supplier).filter(
        Supplier.name.ilike(f'%{supplier_name}%'),
        Supplier.archived_at.is_(None)
    ).first()
    
    if supplier:
        return supplier
    
    # Try reverse partial match (supplier name contains search term)
    suppliers = session.query(Supplier).filter(
        Supplier.archived_at.is_(None)
    ).all()
    
    # Check if any supplier name contains the search term (case insensitive)
    search_lower = supplier_name.lower()
    for s in suppliers:
        if search_lower in s.name.lower() or (s.common_name and search_lower in s.common_name.lower()):
            return s
    
    # Special case mappings
    special_mappings = {
        'insumo forestal': 'Proveedora De Insumos y Maquinaria Forestal S DE RL',
    }
    
    if search_lower in special_mappings:
        mapped_name = special_mappings[search_lower]
        return session.query(Supplier).filter(
            Supplier.name == mapped_name,
            Supplier.archived_at.is_(None)
        ).first()
    
    return None

def fix_unknown_suppliers(csv_path: str, dry_run: bool = True):
    """
    Fix SupplierProduct records assigned to Unknown Supplier.
    
    Args:
        csv_path: Path to the matching CSV file
        dry_run: If True, don't commit changes (just show what would be done)
    """
    session = SessionLocal()
    
    try:
        if not os.path.exists(csv_path):
            print(f"❌ Error: CSV file not found: {csv_path}")
            return
        
        print("=" * 80)
        print("🔧 FIX UNKNOWN SUPPLIER ASSIGNMENTS")
        print("=" * 80)
        print(f"CSV File: {csv_path}")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print("=" * 80)
        
        # Get Unknown Supplier
        unknown_supplier = session.query(Supplier).filter(
            Supplier.name == "Unknown Supplier",
            Supplier.archived_at.is_(None)
        ).first()
        
        if not unknown_supplier:
            print("❌ Unknown Supplier not found in database")
            return
        
        print(f"\n📌 Found 'Unknown Supplier' (ID: {unknown_supplier.id})")
        
        # Get all SupplierProducts assigned to Unknown Supplier
        unknown_supplier_products = session.query(SupplierProduct).filter(
            SupplierProduct.supplier_id == unknown_supplier.id,
            SupplierProduct.archived_at.is_(None)
        ).all()
        
        print(f"📦 Found {len(unknown_supplier_products)} products assigned to Unknown Supplier")
        
        # Read CSV to get supplier names
        product_supplier_map = {}
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_id_str = row.get('stock_product_id', '').strip()
                supplier_name = row.get('supplier_name', '').strip()
                
                if product_id_str:
                    try:
                        product_id = int(product_id_str)
                        product_supplier_map[product_id] = supplier_name
                    except ValueError:
                        pass
        
        print(f"📋 Loaded supplier names for {len(product_supplier_map)} products from CSV")
        
        # Process each unknown supplier product
        print(f"\n🔄 Processing products...")
        print("=" * 80)
        
        fixed = 0
        not_in_csv = 0
        no_supplier_name = 0
        not_found = 0
        errors = []
        
        for i, sp in enumerate(unknown_supplier_products, 1):
            product_id = sp.product_id
            
            # Get supplier name from CSV
            supplier_name = product_supplier_map.get(product_id)
            
            if not supplier_name:
                not_in_csv += 1
                continue
            
            if supplier_name.lower() in ['no supplier match found', 'unknown supplier', '']:
                no_supplier_name += 1
                continue
            
            # Try to find the supplier
            matched_supplier = find_supplier_by_name(session, supplier_name)
            
            if not matched_supplier:
                print(f"[{i}/{len(unknown_supplier_products)}] Product ID {product_id}: '{supplier_name}' - ❌ Not found")
                not_found += 1
                errors.append({
                    'product_id': product_id,
                    'supplier_name': supplier_name
                })
                continue
            
            # Update the supplier
            print(f"[{i}/{len(unknown_supplier_products)}] Product ID {product_id}: '{supplier_name}' → {matched_supplier.name} (ID: {matched_supplier.id})")
            sp.supplier_id = matched_supplier.id
            fixed += 1
        
        # Commit changes
        if not dry_run:
            session.commit()
            print("\n✅ Changes committed to database")
        else:
            session.rollback()
            print("\n⚠️  DRY RUN - No changes committed")
        
        # Summary
        print("\n" + "=" * 80)
        print("📊 SUMMARY")
        print("=" * 80)
        print(f"Total products with Unknown Supplier: {len(unknown_supplier_products)}")
        print(f"✅ Fixed: {fixed}")
        print(f"⚠️  Not in CSV: {not_in_csv}")
        print(f"⚠️  No supplier name in CSV: {no_supplier_name}")
        print(f"❌ Supplier not found: {not_found}")
        
        if errors:
            print(f"\n❌ Suppliers not found in database:")
            unique_suppliers = {}
            for error in errors:
                supplier_name = error['supplier_name']
                if supplier_name not in unique_suppliers:
                    unique_suppliers[supplier_name] = []
                unique_suppliers[supplier_name].append(error['product_id'])
            
            for supplier_name, product_ids in unique_suppliers.items():
                print(f"   - '{supplier_name}' ({len(product_ids)} products): {product_ids[:5]}{'...' if len(product_ids) > 5 else ''}")
        
        remaining = len(unknown_supplier_products) - fixed
        if remaining > 0:
            print(f"\n📌 {remaining} products still assigned to 'Unknown Supplier'")
            print("   These need manual review or supplier creation")
        
        print("\n✅ Processing complete!")
        
    except Exception as e:
        session.rollback()
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        session.close()

def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Fix Unknown Supplier assignments')
    parser.add_argument(
        '--csv-path',
        default='output_suppliers_matched_v5.csv',
        help='Path to matching CSV file'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=True,
        help='Dry run mode (default: True)'
    )
    parser.add_argument(
        '--live',
        action='store_true',
        help='Run in live mode (update actual records)'
    )
    
    args = parser.parse_args()
    
    # If --live is specified, override --dry-run
    dry_run = not args.live
    
    # If csv_path is relative, make it relative to the migrations directory
    if not os.path.isabs(args.csv_path):
        csv_path = os.path.join(
            os.path.dirname(__file__),
            args.csv_path
        )
    else:
        csv_path = args.csv_path
    csv_path = os.path.abspath(csv_path)
    
    if args.live:
        response = input("\n⚠️  This will UPDATE supplier products in the database. Continue? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled.")
            return
    
    fix_unknown_suppliers(csv_path, dry_run=dry_run)

if __name__ == "__main__":
    main()

