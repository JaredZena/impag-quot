#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate BalanceItem records to use supplier_product_id

This script populates the supplier_product_id column in balance_item table
by looking up the corresponding SupplierProduct based on existing supplier_id and product_id.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, BalanceItem, SupplierProduct

def migrate_balance_items():
    """Migrate balance items to use supplier_product_id"""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("📋 MIGRATING BALANCE ITEMS TO SUPPLIER_PRODUCT_ID")
        print("=" * 80)
        
        # Get all balance items that need migration
        balance_items = session.query(BalanceItem).filter(
            BalanceItem.supplier_product_id.is_(None),
            BalanceItem.product_id.isnot(None),
            BalanceItem.supplier_id.isnot(None)
        ).all()
        
        print(f"\n📊 Found {len(balance_items)} balance items to migrate")
        
        migrated_count = 0
        skipped_count = 0
        no_match_items = []
        
        for item in balance_items:
            try:
                # Find matching SupplierProduct based on supplier_id and product_id
                supplier_product = session.query(SupplierProduct).filter(
                    SupplierProduct.supplier_id == item.supplier_id,
                    SupplierProduct.product_id == item.product_id,
                    SupplierProduct.archived_at.is_(None)
                ).first()
                
                if supplier_product:
                    item.supplier_product_id = supplier_product.id
                    migrated_count += 1
                    
                    if migrated_count % 50 == 0:
                        session.commit()
                        print(f"✅ Migrated {migrated_count} balance items...")
                else:
                    # No matching supplier product found
                    no_match_items.append({
                        'balance_item_id': item.id,
                        'balance_id': item.balance_id,
                        'product_id': item.product_id,
                        'supplier_id': item.supplier_id,
                        'quantity': item.quantity
                    })
                    skipped_count += 1
            
            except Exception as e:
                print(f"❌ Error migrating balance item {item.id}: {e}")
                skipped_count += 1
        
        # Final commit
        session.commit()
        
        print("\n" + "=" * 80)
        print("📊 MIGRATION SUMMARY")
        print("=" * 80)
        print(f"✅ Successfully migrated: {migrated_count} balance items")
        print(f"⚠️  Skipped (no match): {skipped_count} balance items")
        
        if no_match_items:
            print("\n⚠️  Balance items without matching SupplierProduct:")
            print("-" * 80)
            for item_info in no_match_items[:10]:  # Show first 10
                print(f"  Balance Item ID: {item_info['balance_item_id']}, "
                      f"Balance ID: {item_info['balance_id']}, "
                      f"Product ID: {item_info['product_id']}, "
                      f"Supplier ID: {item_info['supplier_id']}")
            
            if len(no_match_items) > 10:
                print(f"  ... and {len(no_match_items) - 10} more")
            
            print("\n💡 These items need manual review:")
            print("   - The product-supplier combination may not exist in supplier_product table")
            print("   - Consider creating SupplierProduct records for these combinations")
        
        print("\n💡 Next steps:")
        print("   1. Review any skipped items")
        print("   2. Run migrate_kit_items_to_supplier_product.py")
        print("   3. Update models.py to add supplier_product relationships")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    migrate_balance_items()

