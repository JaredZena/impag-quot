#!/usr/bin/env python3
"""
Migration script to move stock from Product table to SupplierProduct table.

This script will:
1. Find all products with stock > 0
2. For each product, find the cheapest active supplier
3. Assign the product's stock to that supplier's SupplierProduct record
4. Reset the product's stock to 0
5. Log any products with stock but no suppliers (for manual review)

Run this script from the impag-quot directory:
python migrations/migrate_stock_to_suppliers.py
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from decimal import Decimal
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add the parent directory to the Python path to import models
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models import Product, SupplierProduct

def get_database_url():
    """Get database URL from environment or use default."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ Error: DATABASE_URL environment variable not set")
        print("Please set your DATABASE_URL environment variable with your PostgreSQL connection string")
        sys.exit(1)
    return database_url

def migrate_stock_data():
    """Migrate stock data from Product to SupplierProduct table."""
    
    # Create database connection
    database_url = get_database_url()
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    session = SessionLocal()
    
    try:
        print("🚀 Starting stock migration...")
        print(f"📊 Database: {database_url}")
        
        # Find all products with stock > 0
        products_with_stock = session.query(Product).filter(Product.stock > 0).all()
        print(f"📦 Found {len(products_with_stock)} products with existing stock.")
        
        if len(products_with_stock) == 0:
            print("✅ No products with stock found. Migration complete.")
            return
        
        migrated_count = 0
        no_suppliers_count = 0
        products_to_clear = []  # Track which products can be safely cleared
        
        for product in products_with_stock:
            print(f"\n🔄 Processing Product ID: {product.id}")
            print(f"   Name: {product.name}")
            print(f"   Current Stock: {product.stock}")
            
            # Find the cheapest active supplier product for this product
            cheapest_supplier_product = session.query(SupplierProduct).filter(
                SupplierProduct.product_id == product.id,
                SupplierProduct.is_active == True,
                SupplierProduct.archived_at.is_(None)
            ).order_by(SupplierProduct.cost.asc()).first()
            
            if cheapest_supplier_product:
                # Add product's stock to the cheapest supplier product's stock
                old_stock = cheapest_supplier_product.stock or 0
                new_stock = old_stock + product.stock
                cheapest_supplier_product.stock = new_stock
                
                print(f"   ✅ Migrated {product.stock} units to SupplierProduct ID: {cheapest_supplier_product.id}")
                print(f"      Supplier: {cheapest_supplier_product.supplier_id}")
                print(f"      Stock: {old_stock} → {new_stock}")
                print(f"      Cost: {cheapest_supplier_product.cost}")
                
                migrated_count += 1
                # Only clear product stock if migration was successful
                products_to_clear.append(product.id)
            else:
                print(f"   ⚠️  WARNING: Product has stock ({product.stock} units) but no active suppliers!")
                print(f"      Stock will be KEPT in Product table. Manual review needed.")
                no_suppliers_count += 1
        
        # Only clear stock for products that were successfully migrated
        if products_to_clear:
            print(f"\n🧹 Clearing stock for {len(products_to_clear)} successfully migrated products...")
            for product_id in products_to_clear:
                session.query(Product).filter(Product.id == product_id).update({Product.stock: 0})
        
        # Commit all changes
        session.commit()
        
        print(f"\n🎉 Stock migration completed successfully!")
        print(f"📊 Summary:")
        print(f"   • Products processed: {len(products_with_stock)}")
        print(f"   • Successfully migrated: {migrated_count}")
        print(f"   • No suppliers found (stock kept): {no_suppliers_count}")
        print(f"   • Product.stock cleared for: {len(products_to_clear)} products")
        
        if no_suppliers_count > 0:
            print(f"\n⚠️  {no_suppliers_count} products had stock but no active suppliers.")
            print(f"   These products' stock was KEPT in the Product table.")
            print(f"   Consider adding suppliers for these products or investigating the data.")
        
        # Verify the migration
        print(f"\n🔍 Verification:")
        remaining_stock = session.query(Product).filter(Product.stock > 0).count()
        supplier_stock = session.query(SupplierProduct).filter(SupplierProduct.stock > 0).count()
        print(f"   • Products with remaining stock: {remaining_stock}")
        print(f"   • Supplier products with stock: {supplier_stock}")
        
        if migrated_count > 0:
            print("   ✅ Stock migration completed successfully!")
        else:
            print("   ⚠️  No stock was migrated. All products kept their original stock.")
            
    except Exception as e:
        session.rollback()
        print(f"❌ Error during migration: {e}")
        print("🔄 All changes have been rolled back. Your data is safe.")
        raise
    finally:
        session.close()

def verify_migration():
    """Verify the migration results."""
    database_url = get_database_url()
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    session = SessionLocal()
    
    try:
        print(f"\n🔍 Post-migration verification:")
        
        # Check products with stock
        products_with_stock = session.query(Product).filter(Product.stock > 0).count()
        print(f"   • Products with stock > 0: {products_with_stock}")
        
        # Check supplier products with stock
        supplier_products_with_stock = session.query(SupplierProduct).filter(
            SupplierProduct.stock > 0
        ).count()
        print(f"   • Supplier products with stock > 0: {supplier_products_with_stock}")
        
        # Show some examples
        print(f"\n📋 Sample supplier products with stock:")
        sample_supplier_products = session.query(SupplierProduct).filter(
            SupplierProduct.stock > 0
        ).limit(5).all()
        
        for sp in sample_supplier_products:
            product = session.query(Product).filter(Product.id == sp.product_id).first()
            print(f"   • {product.name if product else 'Unknown'} (Supplier {sp.supplier_id}): {sp.stock} units")
            
    finally:
        session.close()

if __name__ == "__main__":
    print("=" * 60)
    print("📦 STOCK MIGRATION SCRIPT")
    print("=" * 60)
    print("This script will migrate stock from Product table to SupplierProduct table.")
    print("Each product's stock will be assigned to its cheapest active supplier.")
    print("=" * 60)
    
    # Ask for confirmation
    response = input("\nDo you want to proceed? (y/N): ").strip().lower()
    if response not in ['y', 'yes']:
        print("❌ Migration cancelled.")
        sys.exit(0)
    
    try:
        migrate_stock_data()
        verify_migration()
        print(f"\n✅ Migration completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)