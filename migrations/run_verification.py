#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run Stock Migration Verification

This script runs the verification queries to check the current state
before and after stock migration.
"""

import os
import sys
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables
load_dotenv()

from models import SessionLocal
from sqlalchemy import text

def run_verification():
    """Run verification queries."""
    session = SessionLocal()
    
    try:
        print("=" * 80)
        print("📊 CURRENT STATE VERIFICATION")
        print("=" * 80)
        
        # Check products with stock
        result = session.execute(text("""
            SELECT 
                COUNT(*) as total_products,
                COUNT(*) FILTER (WHERE stock > 0) as products_with_stock,
                COALESCE(SUM(stock), 0) as total_stock
            FROM product
            WHERE archived_at IS NULL
        """)).fetchone()
        
        print(f"\n📦 PRODUCT TABLE:")
        print(f"   Total products: {result[0]}")
        print(f"   Products with stock > 0: {result[1]}")
        print(f"   Total stock units: {result[2]}")
        
        # Check supplier products
        result = session.execute(text("""
            SELECT 
                COUNT(*) as total_supplier_products,
                COUNT(*) FILTER (WHERE stock > 0) as with_stock,
                COALESCE(SUM(stock), 0) as total_stock
            FROM supplier_product
            WHERE archived_at IS NULL
        """)).fetchone()
        
        print(f"\n🏢 SUPPLIER_PRODUCT TABLE:")
        print(f"   Total supplier products: {result[0]}")
        print(f"   Supplier products with stock > 0: {result[1]}")
        print(f"   Total stock units: {result[2]}")
        
        # Check products with stock but no supplier products
        result = session.execute(text("""
            SELECT COUNT(*)
            FROM product p
            LEFT JOIN supplier_product sp ON sp.product_id = p.id AND sp.archived_at IS NULL
            WHERE p.stock > 0 
                AND p.archived_at IS NULL
            GROUP BY p.id
            HAVING COUNT(sp.id) = 0
        """)).fetchall()
        
        products_without_sp = len(result)
        
        print(f"\n⚠️  PRODUCTS WITH STOCK BUT NO SUPPLIER PRODUCTS: {products_without_sp}")
        
        # Check supplier product assignments
        result = session.execute(text("""
            SELECT 
                s.name,
                COUNT(sp.id) as product_count,
                COUNT(*) FILTER (WHERE sp.stock > 0) as with_stock,
                COALESCE(SUM(sp.stock), 0) as total_stock
            FROM supplier_product sp
            JOIN supplier s ON s.id = sp.supplier_id
            WHERE sp.archived_at IS NULL
            GROUP BY s.id, s.name
            ORDER BY product_count DESC
            LIMIT 10
        """)).fetchall()
        
        print(f"\n🏢 TOP SUPPLIERS BY PRODUCT COUNT:")
        for row in result:
            print(f"   {row[0]}: {row[1]} products ({row[2]} with stock, {row[3]} units)")
        
        # Check Unknown Supplier
        result = session.execute(text("""
            SELECT 
                COUNT(sp.id) as product_count,
                COUNT(*) FILTER (WHERE sp.stock > 0) as with_stock,
                COALESCE(SUM(sp.stock), 0) as total_stock
            FROM supplier_product sp
            JOIN supplier s ON s.id = sp.supplier_id
            WHERE s.name = 'Unknown Supplier'
                AND sp.archived_at IS NULL
        """)).fetchone()
        
        if result and result[0] > 0:
            print(f"\n📌 UNKNOWN SUPPLIER:")
            print(f"   Products assigned: {result[0]}")
            print(f"   Products with stock: {result[1]}")
            print(f"   Total stock units: {result[2]}")
        
        print("\n" + "=" * 80)
        print("✅ Verification complete!")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    run_verification()

