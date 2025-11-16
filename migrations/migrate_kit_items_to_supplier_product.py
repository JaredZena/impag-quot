#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate KitItem records to use supplier_product_id

This script populates the supplier_product_id column in kit_item table
by looking up the corresponding SupplierProduct. If multiple suppliers exist,
it chooses the one with the lowest cost.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, KitItem, SupplierProduct

def migrate_kit_items():
    """Migrate kit items to use supplier_product_id"""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("📋 MIGRATING KIT ITEMS TO SUPPLIER_PRODUCT_ID")
        print("=" * 80)
        
        # Get all kit items that need migration
        kit_items = session.query(KitItem).filter(
            KitItem.supplier_product_id.is_(None),
            KitItem.product_id.isnot(None)
        ).all()
        
        print(f"\n📊 Found {len(kit_items)} kit items to migrate")
        
        migrated_count = 0
        skipped_count = 0
        no_match_items = []
        
        for item in kit_items:
            try:
                # Find matching SupplierProduct(s) based on product_id
                supplier_products = session.query(SupplierProduct).filter(
                    SupplierProduct.product_id == item.product_id,
                    SupplierProduct.archived_at.is_(None),
                    SupplierProduct.is_active == True
                ).all()
                
                if supplier_products:
                    # If multiple suppliers exist, choose the one with lowest cost
                    # Filter out products with no cost
                    products_with_cost = [sp for sp in supplier_products if sp.cost is not None]
                    
                    if products_with_cost:
                        # Choose lowest cost
                        chosen_supplier_product = min(products_with_cost, key=lambda sp: sp.cost)
                    else:
                        # If no products have cost, just choose the first one
                        chosen_supplier_product = supplier_products[0]
                    
                    item.supplier_product_id = chosen_supplier_product.id
                    migrated_count += 1
                    
                    if len(supplier_products) > 1:
                        print(f"  → Kit item {item.id}: Multiple suppliers found, "
                              f"chose supplier {chosen_supplier_product.supplier_id} "
                              f"(cost: {chosen_supplier_product.cost or 'N/A'})")
                    
                    if migrated_count % 50 == 0:
                        session.commit()
                        print(f"✅ Migrated {migrated_count} kit items...")
                else:
                    # No matching supplier product found
                    no_match_items.append({
                        'kit_item_id': item.id,
                        'kit_id': item.kit_id,
                        'product_id': item.product_id,
                        'quantity': item.quantity
                    })
                    skipped_count += 1
            
            except Exception as e:
                print(f"❌ Error migrating kit item {item.id}: {e}")
                skipped_count += 1
        
        # Final commit
        session.commit()
        
        print("\n" + "=" * 80)
        print("📊 MIGRATION SUMMARY")
        print("=" * 80)
        print(f"✅ Successfully migrated: {migrated_count} kit items")
        print(f"⚠️  Skipped (no match): {skipped_count} kit items")
        
        if no_match_items:
            print("\n⚠️  Kit items without matching SupplierProduct:")
            print("-" * 80)
            for item_info in no_match_items[:10]:  # Show first 10
                print(f"  Kit Item ID: {item_info['kit_item_id']}, "
                      f"Kit ID: {item_info['kit_id']}, "
                      f"Product ID: {item_info['product_id']}, "
                      f"Quantity: {item_info['quantity']}")
            
            if len(no_match_items) > 10:
                print(f"  ... and {len(no_match_items) - 10} more")
            
            print("\n💡 These items need manual review:")
            print("   - The product may not have any associated suppliers in supplier_product table")
            print("   - Consider creating SupplierProduct records for these products")
        
        print("\n💡 Next steps:")
        print("   1. Review any skipped items")
        print("   2. Update models.py to add supplier_product relationships")
        print("   3. Update routes to use supplier_product_id")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    migrate_kit_items()

