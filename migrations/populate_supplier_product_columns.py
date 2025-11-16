#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Populate SupplierProduct columns with data from Product table

This script copies all product information from the Product table to the
corresponding SupplierProduct records, making SupplierProduct standalone.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal, Product, SupplierProduct

def populate_columns():
    """Copy product data from Product to SupplierProduct"""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("📋 POPULATING SUPPLIER PRODUCT COLUMNS")
        print("=" * 80)
        
        # Get all active supplier products
        supplier_products = session.query(SupplierProduct).filter(
            SupplierProduct.archived_at.is_(None),
            SupplierProduct.product_id.isnot(None)
        ).all()
        
        print(f"\n📊 Found {len(supplier_products)} supplier products to update")
        
        updated_count = 0
        skipped_count = 0
        
        for sp in supplier_products:
            try:
                # Get the linked product
                product = session.query(Product).filter(Product.id == sp.product_id).first()
                
                if not product:
                    print(f"⚠️  Supplier product {sp.id} has no linked product (product_id: {sp.product_id})")
                    skipped_count += 1
                    continue
                
                # Copy all product columns to supplier product
                sp.name = product.name
                sp.description = product.description
                sp.base_sku = product.base_sku
                
                # Handle SKU - check if this supplier already has this SKU
                existing_sku = session.query(SupplierProduct).filter(
                    SupplierProduct.supplier_id == sp.supplier_id,
                    SupplierProduct.sku == product.sku,
                    SupplierProduct.id != sp.id,
                    SupplierProduct.archived_at.is_(None)
                ).first()
                
                if existing_sku:
                    # Make SKU unique by appending supplier_product id
                    sp.sku = f"{product.sku}-SP{sp.id}"
                    print(f"⚠️  Duplicate SKU detected for supplier {sp.supplier_id}, product SKU: {product.sku}")
                    print(f"   → Using unique SKU: {sp.sku}")
                else:
                    sp.sku = product.sku
                
                sp.category_id = product.category_id
                sp.unit = product.unit.value if product.unit else None
                sp.package_size = product.package_size
                sp.iva = product.iva
                sp.specifications = product.specifications
                sp.default_margin = product.default_margin
                
                updated_count += 1
                
                # Commit every 50 records (smaller batches to handle errors better)
                if updated_count % 50 == 0:
                    try:
                        session.commit()
                        print(f"✅ Updated {updated_count} supplier products...")
                    except Exception as commit_error:
                        print(f"❌ Error committing batch at {updated_count}: {commit_error}")
                        session.rollback()
                        skipped_count += (updated_count % 50)  # Count skipped in this batch
            
            except Exception as e:
                print(f"❌ Error updating supplier product {sp.id}: {e}")
                session.rollback()
                skipped_count += 1
        
        # Final commit
        session.commit()
        
        print("\n" + "=" * 80)
        print("📊 SUMMARY")
        print("=" * 80)
        print(f"✅ Successfully updated: {updated_count} supplier products")
        print(f"⚠️  Skipped: {skipped_count} supplier products")
        print("\n💡 Next steps:")
        print("   1. Verify data in supplier_product table")
        print("   2. Update API endpoints to use supplier_product columns")
        print("   3. Update frontend to display supplier_product data")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    populate_columns()

