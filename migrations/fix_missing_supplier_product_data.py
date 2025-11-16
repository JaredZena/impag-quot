#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fix Missing SupplierProduct Data

This script populates missing product fields in SupplierProduct records
by copying data from their linked Product records.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, Product, SupplierProduct

def fix_missing_data():
    """Populate missing SupplierProduct data from linked Product records"""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("🔧 FIXING MISSING SUPPLIERPRODUCT DATA")
        print("=" * 80)
        
        # Find all SupplierProducts missing product data
        missing_data = session.query(SupplierProduct).filter(
            (SupplierProduct.name.is_(None)) | (SupplierProduct.name == '')
        ).all()
        
        print(f"\n📊 Found {len(missing_data)} SupplierProducts with missing data")
        
        if not missing_data:
            print("✅ No records to fix!")
            return
        
        fixed_count = 0
        skipped_count = 0
        error_count = 0
        
        print(f"\n🔄 Processing records...\n")
        
        for i, sp in enumerate(missing_data, 1):
            try:
                # Check if it has a product_id
                if not sp.product_id:
                    print(f"⚠️  [{i}/{len(missing_data)}] SP ID {sp.id}: No product_id - SKIPPED")
                    skipped_count += 1
                    continue
                
                # Get the linked Product
                product = session.query(Product).filter(Product.id == sp.product_id).first()
                
                if not product:
                    print(f"⚠️  [{i}/{len(missing_data)}] SP ID {sp.id}: Product {sp.product_id} not found - SKIPPED")
                    skipped_count += 1
                    continue
                
                if not product.name or not product.sku:
                    print(f"⚠️  [{i}/{len(missing_data)}] SP ID {sp.id}: Product {sp.product_id} missing data - SKIPPED")
                    skipped_count += 1
                    continue
                
                # Copy data from Product to SupplierProduct
                sp.name = product.name
                sp.description = product.description
                sp.base_sku = product.base_sku
                sp.sku = product.sku
                sp.category_id = product.category_id
                sp.unit = product.unit.value if product.unit else None
                sp.package_size = product.package_size
                sp.iva = product.iva
                sp.specifications = product.specifications
                sp.default_margin = 0.25  # Set default margin
                
                fixed_count += 1
                
                # Show progress every 10 records
                if fixed_count % 10 == 0 or fixed_count == 1:
                    print(f"✅ [{i}/{len(missing_data)}] SP ID {sp.id}: {product.name[:50]}")
                
            except Exception as e:
                print(f"❌ [{i}/{len(missing_data)}] SP ID {sp.id}: Error - {str(e)}")
                error_count += 1
        
        # Commit all changes
        if fixed_count > 0:
            print(f"\n💾 Committing changes to database...")
            session.commit()
            print(f"✅ Changes committed successfully!")
        
        print("\n" + "=" * 80)
        print("📊 SUMMARY")
        print("=" * 80)
        print(f"\n  Total records found: {len(missing_data)}")
        print(f"  ✅ Fixed: {fixed_count}")
        print(f"  ⚠️  Skipped: {skipped_count}")
        print(f"  ❌ Errors: {error_count}")
        
        if fixed_count > 0:
            print(f"\n🎉 Successfully populated {fixed_count} SupplierProduct records!")
        
        # Verify
        print("\n" + "=" * 80)
        print("🔍 VERIFICATION")
        print("=" * 80)
        
        remaining_missing = session.query(SupplierProduct).filter(
            (SupplierProduct.name.is_(None)) | (SupplierProduct.name == '')
        ).count()
        
        print(f"\n  SupplierProducts still missing data: {remaining_missing}")
        
        if remaining_missing == 0:
            print(f"  ✅ All SupplierProducts now have product data!")
        else:
            print(f"  ℹ️  {remaining_missing} records still missing data (likely orphaned)")
        
        print("\n" + "=" * 80)
        print("✅ FIX COMPLETE")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    print("\n⚠️  This script will populate missing SupplierProduct data from Product records.")
    print("   This is safe and will only update records that are missing data.\n")
    
    response = input("Continue? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        fix_missing_data()
    else:
        print("Cancelled.")

