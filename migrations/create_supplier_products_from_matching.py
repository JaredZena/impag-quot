#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create Supplier Products from Manual Matching CSV

This script reads the output_suppliers_matched_v5.csv file and creates
SupplierProduct records for products that have been matched to suppliers.

Products without a supplier_id will be skipped (cannot create SupplierProduct
without a supplier).
"""

import os
import sys
import csv
from decimal import Decimal
from typing import Optional
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import Product, Supplier, SupplierProduct, SessionLocal
from sqlalchemy.orm import Session

def get_or_create_unknown_supplier(session: Session) -> Supplier:
    """
    Get or create the default 'Unknown Supplier' for products without a supplier match.
    
    Returns:
        Supplier object (existing or newly created)
    """
    # Check if unknown supplier already exists
    unknown_supplier = session.query(Supplier).filter(
        Supplier.name == "Unknown Supplier",
        Supplier.archived_at.is_(None)
    ).first()
    
    if unknown_supplier:
        return unknown_supplier
    
    # Create new unknown supplier
    unknown_supplier = Supplier(
        name="Unknown Supplier",
        common_name="Unknown Supplier",
        legal_name="Unknown Supplier",
        rfc=None,
        description="Default supplier for products without a matched supplier. Products assigned to this supplier need manual review to find their actual supplier."
    )
    
    session.add(unknown_supplier)
    session.flush()
    print(f"✅ Created default 'Unknown Supplier' (ID: {unknown_supplier.id})")
    return unknown_supplier

def create_supplier_product(
    session: Session,
    product_id: int,
    supplier_id: int,
    product_price: Optional[float] = None
) -> Optional[SupplierProduct]:
    """
    Create a supplier product relationship.
    
    Returns:
        Created SupplierProduct or None if already exists
    """
    # Check if supplier product already exists
    existing = session.query(SupplierProduct).filter(
        SupplierProduct.product_id == product_id,
        SupplierProduct.supplier_id == supplier_id,
        SupplierProduct.archived_at.is_(None)
    ).first()
    
    if existing:
        return None  # Already exists
    
    # Create new supplier product
    supplier_product = SupplierProduct(
        supplier_id=supplier_id,
        product_id=product_id,
        cost=Decimal(str(product_price)) if product_price else None,
        currency='MXN',  # Default to MXN
        stock=0,  # Stock will be migrated separately
        is_active=True
    )
    
    session.add(supplier_product)
    session.flush()
    return supplier_product

def process_matching_csv(csv_path: str, dry_run: bool = True, use_unknown_supplier: bool = True):
    """
    Process the matching CSV and create supplier products.
    
    Args:
        csv_path: Path to the matching CSV file
        dry_run: If True, don't commit changes (just show what would be done)
        use_unknown_supplier: If True, assign products without supplier to 'Unknown Supplier'
    """
    session = SessionLocal()
    
    try:
        # Get or create unknown supplier if needed
        unknown_supplier = None
        unknown_supplier_id = None
        if use_unknown_supplier:
            unknown_supplier = get_or_create_unknown_supplier(session)
            unknown_supplier_id = unknown_supplier.id  # Get ID before any commit
            if not dry_run:
                session.commit()  # Commit the unknown supplier creation
        if not os.path.exists(csv_path):
            print(f"❌ Error: CSV file not found: {csv_path}")
            return
        
        print("=" * 80)
        print("🔗 CREATE SUPPLIER PRODUCTS FROM MATCHING")
        print("=" * 80)
        print(f"CSV File: {csv_path}")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print("=" * 80)
        
        # Read CSV
        matches = []
        skipped_no_supplier = []
        skipped_invalid = []
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                product_id_str = row.get('stock_product_id', '').strip()
                supplier_id_str = row.get('supplier_id', '').strip()
                product_name = row.get('stock_product_name', '').strip()
                supplier_name = row.get('supplier_name', '').strip()
                
                # Skip if no product ID
                if not product_id_str:
                    skipped_invalid.append({
                        'row': row,
                        'reason': 'No product_id'
                    })
                    continue
                
                try:
                    product_id = int(product_id_str)
                except ValueError:
                    skipped_invalid.append({
                        'row': row,
                        'reason': f'Invalid product_id: {product_id_str}'
                    })
                    continue
                
                # Handle products without supplier ID
                if not supplier_id_str or supplier_id_str.lower() in ['', 'no supplier match found', 'null', 'none']:
                    if use_unknown_supplier and unknown_supplier_id:
                        # Use unknown supplier for products without a match
                        matches.append({
                            'product_id': product_id,
                            'product_name': product_name,
                            'supplier_id': unknown_supplier_id,
                            'supplier_name': 'Unknown Supplier',
                            'is_unknown': True
                        })
                    else:
                        # Skip if not using unknown supplier
                        skipped_no_supplier.append({
                            'product_id': product_id,
                            'product_name': product_name,
                            'supplier_name': supplier_name,
                            'reason': 'No supplier_id provided'
                        })
                    continue
                
                try:
                    supplier_id = int(supplier_id_str)
                except ValueError:
                    skipped_invalid.append({
                        'row': row,
                        'reason': f'Invalid supplier_id: {supplier_id_str}'
                    })
                    continue
                
                matches.append({
                    'product_id': product_id,
                    'product_name': product_name,
                    'supplier_id': supplier_id,
                    'supplier_name': supplier_name,
                    'is_unknown': False
                })
        
        # Count unknown vs known suppliers
        unknown_count = sum(1 for m in matches if m.get('is_unknown', False))
        known_count = len(matches) - unknown_count
        
        print(f"\n📊 Summary:")
        print(f"   Total rows in CSV: {len(matches) + len(skipped_no_supplier) + len(skipped_invalid)}")
        print(f"   Products with supplier matches: {known_count}")
        if use_unknown_supplier and unknown_supplier_id:
            print(f"   Products assigned to 'Unknown Supplier': {unknown_count}")
        print(f"   Products without supplier (will be skipped): {len(skipped_no_supplier)}")
        print(f"   Invalid rows: {len(skipped_invalid)}")
        
        if use_unknown_supplier and unknown_supplier_id:
            print(f"\n📌 Using 'Unknown Supplier' (ID: {unknown_supplier_id}) for products without matches")
        
        if skipped_no_supplier:
            print(f"\n⚠️  Products without supplier (will be skipped):")
            for item in skipped_no_supplier[:10]:  # Show first 10
                print(f"   - Product ID {item['product_id']}: {item['product_name'][:60]}...")
            if len(skipped_no_supplier) > 10:
                print(f"   ... and {len(skipped_no_supplier) - 10} more")
        
        # Process matches
        print(f"\n🔄 Processing {len(matches)} matches...")
        print("=" * 80)
        
        created = 0
        already_exists = 0
        not_found = []
        errors = []
        
        for i, match in enumerate(matches, 1):
            product_id = match['product_id']
            supplier_id = match['supplier_id']
            product_name = match['product_name']
            supplier_name = match['supplier_name']
            
            is_unknown = match.get('is_unknown', False)
            print(f"\n[{i}/{len(matches)}] Product ID {product_id}: {product_name[:60]}...")
            if is_unknown:
                print(f"   Supplier: {supplier_name} (ID: {supplier_id}) ⚠️  [UNKNOWN - needs review]")
            else:
                print(f"   Supplier: {supplier_name} (ID: {supplier_id})")
            
            # Verify product exists
            product = session.query(Product).filter(Product.id == product_id).first()
            if not product:
                print(f"   ❌ Product not found in database")
                not_found.append({
                    'product_id': product_id,
                    'product_name': product_name
                })
                continue
            
            # Verify supplier exists
            supplier = session.query(Supplier).filter(Supplier.id == supplier_id).first()
            if not supplier:
                print(f"   ❌ Supplier not found in database")
                not_found.append({
                    'supplier_id': supplier_id,
                    'supplier_name': supplier_name
                })
                continue
            
            # Get product price if available
            product_price = float(product.price) if product.price else None
            
            # Create supplier product
            try:
                supplier_product = create_supplier_product(
                    session,
                    product_id,
                    supplier_id,
                    product_price
                )
                
                if supplier_product:
                    print(f"   ✅ Created supplier product ID: {supplier_product.id}")
                    if product_price:
                        print(f"      Cost: ${product_price}")
                    created += 1
                else:
                    print(f"   ⚠️  Supplier product already exists")
                    already_exists += 1
            except Exception as e:
                print(f"   ❌ Error: {e}")
                errors.append({
                    'product_id': product_id,
                    'supplier_id': supplier_id,
                    'error': str(e)
                })
        
        # Commit if not dry run
        if not dry_run:
            session.commit()
            print("\n✅ Changes committed to database")
        else:
            session.rollback()
            print("\n⚠️  DRY RUN - No changes committed")
        
        # Final summary
        print("\n" + "=" * 80)
        print("📊 FINAL SUMMARY")
        print("=" * 80)
        
        print(f"✅ Created: {created}")
        print(f"⚠️  Already existed: {already_exists}")
        print(f"❌ Not found: {len(not_found)}")
        print(f"❌ Errors: {len(errors)}")
        print(f"⏭️  Skipped (no supplier): {len(skipped_no_supplier)}")
        if use_unknown_supplier and unknown_supplier_id:
            print(f"📌 Assigned to 'Unknown Supplier': {unknown_count}")
        
        if not_found:
            print(f"\n⚠️  Items not found in database:")
            for item in not_found[:5]:
                if 'product_id' in item:
                    print(f"   - Product ID {item['product_id']}: {item.get('product_name', 'N/A')}")
                else:
                    print(f"   - Supplier ID {item['supplier_id']}: {item.get('supplier_name', 'N/A')}")
        
        if errors:
            print(f"\n❌ Errors occurred:")
            for item in errors[:5]:
                print(f"   - Product {item['product_id']} / Supplier {item['supplier_id']}: {item['error']}")
        
        if skipped_no_supplier:
            print(f"\n⏭️  Products without supplier (skipped): {len(skipped_no_supplier)}")
            if not use_unknown_supplier:
                print("   These products cannot have SupplierProduct created without a supplier_id.")
                print("   They will remain in the Product table without a supplier relationship.")
        
        if use_unknown_supplier and unknown_supplier_id and unknown_count > 0:
            print(f"\n📌 Note: {unknown_count} products were assigned to 'Unknown Supplier' (ID: {unknown_supplier_id})")
            print("   These products need manual review to find their actual suppliers.")
            print("   You can update them later by changing the supplier_id in the SupplierProduct table.")
        
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
    
    parser = argparse.ArgumentParser(description='Create supplier products from matching CSV')
    parser.add_argument(
        '--csv-path',
        default='migrations/output_suppliers_matched_v5.csv',
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
        help='Run in live mode (create actual records)'
    )
    parser.add_argument(
        '--no-unknown-supplier',
        action='store_true',
        help='Do not create/use Unknown Supplier for products without matches'
    )
    
    args = parser.parse_args()
    
    # If --live is specified, override --dry-run
    dry_run = not args.live
    use_unknown_supplier = not args.no_unknown_supplier
    
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
        response = input("\n⚠️  This will CREATE supplier products in the database. Continue? (y/N): ")
        if response.lower() != 'y':
            print("❌ Cancelled.")
            return
    
    process_matching_csv(csv_path, dry_run=dry_run, use_unknown_supplier=use_unknown_supplier)

if __name__ == "__main__":
    main()

