#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify SupplierProduct Data Quality

This script compares data between the Product table and SupplierProduct table
to verify the migration was successful and SupplierProduct is now the correct source of truth.
"""

import os
import sys
from dotenv import load_dotenv
from decimal import Decimal

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, Product, SupplierProduct, Supplier, ProductCategory
from sqlalchemy import func

def verify_supplier_product_data():
    """Comprehensive verification of SupplierProduct table data"""
    session = SessionLocal()
    
    try:
        print("=" * 100)
        print("🔍 SUPPLIER PRODUCT DATA VERIFICATION")
        print("=" * 100)
        
        # ====================================================================================
        # SECTION 1: Basic Counts
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 1: BASIC COUNTS")
        print("=" * 100)
        
        total_products = session.query(Product).count()
        total_supplier_products = session.query(SupplierProduct).count()
        sp_with_product_data = session.query(SupplierProduct).filter(
            SupplierProduct.name.isnot(None)
        ).count()
        sp_without_product_data = total_supplier_products - sp_with_product_data
        
        sp_with_product_id = session.query(SupplierProduct).filter(
            SupplierProduct.product_id.isnot(None)
        ).count()
        sp_without_product_id = session.query(SupplierProduct).filter(
            SupplierProduct.product_id.is_(None)
        ).count()
        
        print(f"\n📦 Product Table:")
        print(f"   Total Products: {total_products}")
        
        print(f"\n📦 SupplierProduct Table:")
        print(f"   Total SupplierProducts: {total_supplier_products}")
        print(f"   With product data (name populated): {sp_with_product_data} ({sp_with_product_data/total_supplier_products*100:.1f}%)")
        print(f"   Without product data: {sp_without_product_data} ({sp_without_product_data/total_supplier_products*100:.1f}%)")
        print(f"\n🔗 Product Table Linkage:")
        print(f"   Linked to Product table (product_id NOT NULL): {sp_with_product_id}")
        print(f"   Standalone (product_id IS NULL): {sp_without_product_id} ⭐ NEW ARCHITECTURE")
        
        # ====================================================================================
        # SECTION 2: Product Field Population in SupplierProduct
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 2: PRODUCT FIELD POPULATION")
        print("=" * 100)
        
        print("\nChecking which product fields are populated in SupplierProduct table:")
        
        fields_to_check = [
            ('name', 'Product Name'),
            ('description', 'Description'),
            ('base_sku', 'Base SKU'),
            ('sku', 'SKU'),
            ('category_id', 'Category'),
            ('unit', 'Unit'),
            ('iva', 'IVA'),
            ('default_margin', 'Default Margin'),
        ]
        
        print(f"\n{'Field':<20} {'Populated':<15} {'Percentage':<15} {'Status'}")
        print("-" * 70)
        
        for field_name, display_name in fields_to_check:
            count = session.query(SupplierProduct).filter(
                getattr(SupplierProduct, field_name).isnot(None)
            ).count()
            percentage = (count / total_supplier_products * 100) if total_supplier_products > 0 else 0
            status = "✅ GOOD" if percentage > 80 else "⚠️ NEEDS REVIEW" if percentage > 50 else "❌ POOR"
            print(f"{display_name:<20} {count:<15} {percentage:>6.1f}%       {status}")
        
        # ====================================================================================
        # SECTION 3: Data Consistency Check (Product vs SupplierProduct)
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 3: DATA CONSISTENCY CHECK")
        print("=" * 100)
        
        print("\nComparing data between Product and SupplierProduct for linked records...")
        
        # Get linked records (where product_id is not null)
        linked_sps = session.query(SupplierProduct).filter(
            SupplierProduct.product_id.isnot(None),
            SupplierProduct.name.isnot(None)
        ).limit(100).all()
        
        if linked_sps:
            mismatches = {
                'name': 0,
                'sku': 0,
                'category_id': 0,
                'unit': 0,
            }
            
            total_checked = 0
            
            for sp in linked_sps:
                product = session.query(Product).filter(Product.id == sp.product_id).first()
                if product:
                    total_checked += 1
                    
                    # Check name
                    if sp.name != product.name:
                        mismatches['name'] += 1
                    
                    # Check SKU
                    if sp.sku and sp.sku != product.sku:
                        mismatches['sku'] += 1
                    
                    # Check category
                    if sp.category_id != product.category_id:
                        mismatches['category_id'] += 1
                    
                    # Check unit (convert enum to string)
                    product_unit = product.unit.value if product.unit else None
                    if sp.unit != product_unit:
                        mismatches['unit'] += 1
            
            print(f"\n✅ Checked {total_checked} linked SupplierProduct records against Product table:")
            print(f"\n{'Field':<20} {'Mismatches':<15} {'Match Rate'}")
            print("-" * 50)
            
            for field, count in mismatches.items():
                match_rate = ((total_checked - count) / total_checked * 100) if total_checked > 0 else 0
                status = "✅" if count == 0 else "⚠️"
                print(f"{status} {field:<18} {count:<15} {match_rate:>6.1f}%")
        else:
            print("⚠️  No linked records found to compare")
        
        # ====================================================================================
        # SECTION 4: Standalone SupplierProducts (NEW ARCHITECTURE)
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 4: STANDALONE SUPPLIER PRODUCTS (NEW ARCHITECTURE)")
        print("=" * 100)
        
        standalone_sps = session.query(SupplierProduct).filter(
            SupplierProduct.product_id.is_(None)
        ).all()
        
        print(f"\n⭐ Found {len(standalone_sps)} standalone SupplierProducts (product_id = NULL)")
        print("   These represent the NEW architecture where SupplierProduct is the source of truth.")
        
        if standalone_sps:
            # Check data completeness for standalone records
            standalone_complete = 0
            standalone_incomplete = 0
            
            required_fields = ['name', 'sku', 'cost', 'supplier_id']
            
            for sp in standalone_sps:
                is_complete = all([
                    sp.name is not None,
                    sp.sku is not None,
                    sp.cost is not None,
                    sp.supplier_id is not None
                ])
                
                if is_complete:
                    standalone_complete += 1
                else:
                    standalone_incomplete += 1
            
            print(f"\n   Complete (all required fields): {standalone_complete}")
            print(f"   Incomplete (missing data): {standalone_incomplete}")
            
            if standalone_complete > 0:
                print(f"\n   ✅ {standalone_complete} standalone records are production-ready!")
            
            # Show sample standalone records
            if len(standalone_sps) > 0:
                print("\n   📋 Sample standalone SupplierProduct records:")
                print(f"   {'ID':<8} {'Name':<40} {'SKU':<20} {'Supplier'}")
                print("   " + "-" * 100)
                
                for sp in standalone_sps[:5]:
                    supplier_name = sp.supplier.name if sp.supplier else "Unknown"
                    name_display = (sp.name[:37] + "...") if sp.name and len(sp.name) > 40 else (sp.name or "N/A")
                    sku_display = (sp.sku[:17] + "...") if sp.sku and len(sp.sku) > 20 else (sp.sku or "N/A")
                    print(f"   {sp.id:<8} {name_display:<40} {sku_display:<20} {supplier_name}")
        
        # ====================================================================================
        # SECTION 5: Orphaned Data Detection
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 5: ORPHANED DATA DETECTION")
        print("=" * 100)
        
        # Products without any SupplierProduct
        products_without_sp = session.query(Product).outerjoin(
            SupplierProduct, Product.id == SupplierProduct.product_id
        ).filter(
            SupplierProduct.id.is_(None)
        ).count()
        
        # SupplierProducts without valid supplier
        sp_without_supplier = session.query(SupplierProduct).filter(
            SupplierProduct.supplier_id.is_(None)
        ).count()
        
        # SupplierProducts with invalid product_id
        sp_with_invalid_product = session.query(SupplierProduct).outerjoin(
            Product, SupplierProduct.product_id == Product.id
        ).filter(
            SupplierProduct.product_id.isnot(None),
            Product.id.is_(None)
        ).count()
        
        print(f"\n🔍 Orphaned Data:")
        print(f"   Products without any SupplierProduct: {products_without_sp}")
        print(f"   SupplierProducts without Supplier: {sp_without_supplier}")
        print(f"   SupplierProducts with invalid product_id: {sp_with_invalid_product}")
        
        if products_without_sp > 0:
            print(f"\n   ℹ️  Note: Products without SupplierProducts are expected if:")
            print(f"      - They're being phased out")
            print(f"      - They're legacy data not yet migrated")
            print(f"      - SupplierProduct is now the primary table (NEW ARCHITECTURE)")
        
        # ====================================================================================
        # SECTION 6: Recent Activity
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 6: RECENT ACTIVITY (LAST 24 HOURS)")
        print("=" * 100)
        
        from datetime import datetime, timedelta
        yesterday = datetime.now() - timedelta(days=1)
        
        recent_products = session.query(Product).filter(
            Product.created_at >= yesterday
        ).count()
        
        recent_sp = session.query(SupplierProduct).filter(
            SupplierProduct.created_at >= yesterday
        ).count()
        
        recent_sp_standalone = session.query(SupplierProduct).filter(
            SupplierProduct.created_at >= yesterday,
            SupplierProduct.product_id.is_(None)
        ).count()
        
        recent_sp_linked = session.query(SupplierProduct).filter(
            SupplierProduct.created_at >= yesterday,
            SupplierProduct.product_id.isnot(None)
        ).count()
        
        print(f"\n📅 Created in last 24 hours:")
        print(f"   Products: {recent_products}")
        print(f"   SupplierProducts (total): {recent_sp}")
        print(f"   SupplierProducts (standalone, product_id = NULL): {recent_sp_standalone} ⭐")
        print(f"   SupplierProducts (linked to Product): {recent_sp_linked}")
        
        if recent_products > 0:
            print(f"\n   ⚠️  WARNING: {recent_products} new Product records created!")
            print(f"      This suggests old code paths may still be active.")
            print(f"      Review quotation_processor.py and other creation endpoints.")
        
        if recent_sp_standalone > 0:
            print(f"\n   ✅ GOOD: {recent_sp_standalone} standalone SupplierProducts created!")
            print(f"      New architecture is working correctly.")
        
        # ====================================================================================
        # SECTION 7: Migration Recommendations
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 7: MIGRATION STATUS & RECOMMENDATIONS")
        print("=" * 100)
        
        print("\n🎯 Migration Status:")
        
        # Calculate migration completeness
        migration_score = 0
        max_score = 5
        
        # Check 1: SupplierProduct has product fields
        if sp_with_product_data / total_supplier_products > 0.8:
            print("   ✅ SupplierProduct table has product fields populated (>80%)")
            migration_score += 1
        else:
            print(f"   ⚠️  SupplierProduct table product fields incomplete ({sp_with_product_data/total_supplier_products*100:.1f}%)")
        
        # Check 2: Standalone records exist
        if sp_without_product_id > 0:
            print(f"   ✅ Standalone SupplierProducts exist ({sp_without_product_id} records)")
            migration_score += 1
        else:
            print("   ⚠️  No standalone SupplierProducts found")
        
        # Check 3: No recent Product creation
        if recent_products == 0:
            print("   ✅ No recent Product table growth (old code paths inactive)")
            migration_score += 1
        else:
            print(f"   ⚠️  Product table still growing ({recent_products} in last 24h)")
        
        # Check 4: Recent standalone creation
        if recent_sp_standalone > 0:
            print(f"   ✅ Recent standalone SupplierProduct creation ({recent_sp_standalone} in last 24h)")
            migration_score += 1
        else:
            print("   ⚠️  No recent standalone SupplierProduct creation")
        
        # Check 5: Low orphaned data
        if sp_without_supplier < 10 and sp_with_invalid_product == 0:
            print("   ✅ Minimal orphaned data")
            migration_score += 1
        else:
            print(f"   ⚠️  Some orphaned data detected")
        
        print(f"\n📈 Migration Completeness: {migration_score}/{max_score} ({migration_score/max_score*100:.0f}%)")
        
        if migration_score == max_score:
            print("\n🎉 ✅ MIGRATION COMPLETE AND SUCCESSFUL!")
            print("   SupplierProduct is now the source of truth.")
            print("   Ready for production deployment!")
        elif migration_score >= 3:
            print("\n⚠️  MIGRATION MOSTLY COMPLETE")
            print("   Review warnings above before production deployment.")
        else:
            print("\n❌ MIGRATION INCOMPLETE")
            print("   Address issues above before deploying to production.")
        
        # ====================================================================================
        # SECTION 8: Action Items
        # ====================================================================================
        print("\n" + "=" * 100)
        print("📊 SECTION 8: RECOMMENDED ACTIONS")
        print("=" * 100)
        
        actions = []
        
        if sp_without_product_data > 100:
            actions.append(f"⚠️  Populate product fields for {sp_without_product_data} SupplierProducts")
        
        if recent_products > 0:
            actions.append(f"⚠️  Investigate why {recent_products} Product records were created recently")
            actions.append("   → Check quotation_processor.py")
            actions.append("   → Check any direct Product creation endpoints")
        
        if sp_without_supplier > 0:
            actions.append(f"⚠️  Fix {sp_without_supplier} SupplierProducts without a supplier")
        
        if sp_with_invalid_product > 0:
            actions.append(f"⚠️  Fix {sp_with_invalid_product} SupplierProducts with invalid product_id")
        
        if products_without_sp > 50:
            actions.append(f"ℹ️  Consider archiving {products_without_sp} orphaned Products")
        
        if not actions:
            print("\n✅ No immediate actions required!")
            print("   System is healthy and migration is complete.")
        else:
            print("\n📋 Action Items:")
            for action in actions:
                print(f"   {action}")
        
        print("\n" + "=" * 100)
        print("✅ VERIFICATION COMPLETE")
        print("=" * 100)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    verify_supplier_product_data()

