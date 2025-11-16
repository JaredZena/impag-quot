#!/usr/bin/env python3
"""
Script to fix product prices for products that don't have prices set.
This is needed for SQLite databases that don't have PostgreSQL triggers.
"""
from decimal import Decimal
from sqlalchemy import func, case
from models import Product, SupplierProduct, SessionLocal

def update_product_prices():
    """Update all products that don't have prices set."""
    session = SessionLocal()
    try:
        # Get all products without prices
        products_without_prices = session.query(Product).filter(
            Product.price.is_(None),
            Product.is_active == True
        ).all()
        
        print(f"Found {len(products_without_prices)} products without prices")
        
        # Set default margin for products that don't have one
        products_without_margin = session.query(Product).filter(
            Product.default_margin.is_(None),
            Product.is_active == True
        ).all()
        
        print(f"Found {len(products_without_margin)} products without default margin")
        
        for product in products_without_margin:
            product.default_margin = 0.25  # 25% default margin
            print(f"  Set default margin for: {product.name}")
        
        session.flush()
        
        # Calculate total shipping cost based on method
        total_shipping_cost = case(
            (SupplierProduct.shipping_method == 'DIRECT', func.coalesce(SupplierProduct.shipping_cost_direct, 0)),
            else_=(
                func.coalesce(SupplierProduct.shipping_stage1_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage2_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage3_cost, 0) +
                func.coalesce(SupplierProduct.shipping_stage4_cost, 0)
            )
        )
        
        # Update prices for products without prices
        updated_count = 0
        skipped_count = 0
        
        for product in products_without_prices:
            if product.default_margin is None:
                print(f"  Skipping {product.name}: No default margin")
                skipped_count += 1
                continue
            
            # Get the lowest cost from all active supplier-product relationships
            lowest_cost_result = session.query(
                func.min(SupplierProduct.cost + total_shipping_cost).label('lowest_cost')
            ).filter(
                SupplierProduct.product_id == product.id,
                SupplierProduct.is_active == True,
                SupplierProduct.cost.isnot(None),
                SupplierProduct.cost > 0
            ).first()
            
            if lowest_cost_result and lowest_cost_result.lowest_cost:
                lowest_cost = Decimal(str(lowest_cost_result.lowest_cost))
                margin = Decimal(str(product.default_margin))
                
                # Calculate price with margin: price = cost / (1 - margin)
                if margin < 1:  # Margin must be less than 100%
                    calculated_price = lowest_cost / (Decimal('1') - margin)
                    product.calculated_price = float(calculated_price)
                    product.price = float(calculated_price)
                    print(f"  ✓ Updated {product.name}: ${calculated_price:.2f} (margin: {margin*100}%)")
                    updated_count += 1
                else:
                    print(f"  Skipping {product.name}: Invalid margin ({margin*100}%)")
                    skipped_count += 1
            else:
                print(f"  Skipping {product.name}: No supplier cost found")
                skipped_count += 1
        
        session.commit()
        print(f"\n✓ Updated {updated_count} products")
        print(f"⚠ Skipped {skipped_count} products")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        session.rollback()
        raise
    finally:
        session.close()

if __name__ == "__main__":
    update_product_prices()







