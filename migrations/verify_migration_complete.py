#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify Migration Completion

This script verifies that the migration from Product to SupplierProduct is complete:
- Checks all KitItems have supplier_product_id
- Checks all BalanceItems have supplier_product_id
- Reports migration status
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, KitItem, BalanceItem, SupplierProduct, Product

def verify_migration():
    """Verify migration completion"""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("🔍 VERIFYING MIGRATION COMPLETION")
        print("=" * 80)
        
        # Check KitItems
        print("\n📦 Checking KitItems...")
        total_kit_items = session.query(KitItem).count()
        kit_items_with_sp_id = session.query(KitItem).filter(
            KitItem.supplier_product_id.isnot(None)
        ).count()
        kit_items_without_sp_id = total_kit_items - kit_items_with_sp_id
        
        print(f"  Total KitItems: {total_kit_items}")
        print(f"  With supplier_product_id: {kit_items_with_sp_id}")
        print(f"  Without supplier_product_id: {kit_items_without_sp_id}")
        
        if kit_items_without_sp_id > 0:
            print(f"  ⚠️  {kit_items_without_sp_id} KitItems still need migration")
        else:
            print(f"  ✅ All KitItems have supplier_product_id")
        
        # Check BalanceItems
        print("\n📊 Checking BalanceItems...")
        total_balance_items = session.query(BalanceItem).count()
        balance_items_with_sp_id = session.query(BalanceItem).filter(
            BalanceItem.supplier_product_id.isnot(None)
        ).count()
        balance_items_without_sp_id = total_balance_items - balance_items_with_sp_id
        
        print(f"  Total BalanceItems: {total_balance_items}")
        print(f"  With supplier_product_id: {balance_items_with_sp_id}")
        print(f"  Without supplier_product_id: {balance_items_without_sp_id}")
        
        if balance_items_without_sp_id > 0:
            print(f"  ⚠️  {balance_items_without_sp_id} BalanceItems still need migration")
        else:
            print(f"  ✅ All BalanceItems have supplier_product_id")
        
        # Check SupplierProducts
        print("\n🏭 Checking SupplierProducts...")
        total_supplier_products = session.query(SupplierProduct).count()
        sp_with_product_fields = session.query(SupplierProduct).filter(
            SupplierProduct.name.isnot(None)
        ).count()
        sp_without_product_fields = total_supplier_products - sp_with_product_fields
        
        print(f"  Total SupplierProducts: {total_supplier_products}")
        print(f"  With product fields (name): {sp_with_product_fields}")
        print(f"  Without product fields: {sp_without_product_fields}")
        
        if sp_without_product_fields > 0:
            print(f"  ⚠️  {sp_without_product_fields} SupplierProducts don't have product data")
        else:
            print(f"  ✅ All SupplierProducts have product fields populated")
        
        # Check Products table
        print("\n📄 Checking Products table...")
        total_products = session.query(Product).count()
        active_products = session.query(Product).filter(
            Product.archived_at.is_(None)
        ).count()
        
        print(f"  Total Products: {total_products}")
        print(f"  Active Products: {active_products}")
        print(f"  💡 Note: Product table is kept for backward compatibility")
        
        # Overall status
        print("\n" + "=" * 80)
        print("📋 MIGRATION STATUS")
        print("=" * 80)
        
        all_migrated = (
            kit_items_without_sp_id == 0 and
            balance_items_without_sp_id == 0 and
            sp_without_product_fields == 0
        )
        
        if all_migrated:
            print("✅ ✅ ✅ MIGRATION COMPLETE ✅ ✅ ✅")
            print()
            print("All data has been successfully migrated to SupplierProduct!")
            print()
            print("✅ KitItems: All have supplier_product_id")
            print("✅ BalanceItems: All have supplier_product_id")
            print("✅ SupplierProducts: All have product fields populated")
            print()
            print("💡 Next steps:")
            print("   1. Test the local app thoroughly")
            print("   2. Deploy to production")
            print("   3. Monitor production for issues")
            print("   4. After validation, consider removing Product table in Phase 2")
        else:
            print("⚠️  MIGRATION INCOMPLETE")
            print()
            print("Some records still need to be migrated:")
            if kit_items_without_sp_id > 0:
                print(f"  ❌ {kit_items_without_sp_id} KitItems missing supplier_product_id")
            if balance_items_without_sp_id > 0:
                print(f"  ❌ {balance_items_without_sp_id} BalanceItems missing supplier_product_id")
            if sp_without_product_fields > 0:
                print(f"  ❌ {sp_without_product_fields} SupplierProducts missing product fields")
            print()
            print("💡 Please review the migration scripts and run them again:")
            print("   - migrate_kit_items_to_supplier_product.py")
            print("   - migrate_balance_items_to_supplier_product.py")
            print("   - populate_supplier_product_columns.py")
        
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    verify_migration()

