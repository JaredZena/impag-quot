#!/usr/bin/env python3
"""
Verify stock import results
"""

from models import SessionLocal, Product
from sqlalchemy import func
from datetime import date

def verify_import():
    db = SessionLocal()
    try:
        # Total products
        total = db.query(Product).count()
        print(f'✅ Total products in database: {total}')
        
        # Products imported today
        today_products = db.query(Product).filter(func.date(Product.created_at) == date.today()).count()
        print(f'✅ Products imported today: {today_products}')
        
        # Check specific products from your CSV
        test_products = [
            'PROTECTOR DE CULTIVO',
            'Gancho de tutorado',
            'POLYPATCH 10'
        ]
        
        print(f'\n📦 Sample products verification:')
        for name_part in test_products:
            product = db.query(Product).filter(Product.name.like(f'%{name_part}%')).first()
            if product:
                print(f'   ✅ {product.name[:60]}... Stock: {product.stock}, Price: ${product.price}')
        
        # Products with highest stock
        print(f'\n📈 Top 5 products by stock:')
        top_stock = db.query(Product).filter(Product.stock > 0).order_by(Product.stock.desc()).limit(5)
        for p in top_stock:
            print(f'   📦 {p.name[:50]}... Stock: {p.stock}')
        
        # Total inventory value
        total_value = db.query(func.sum(Product.stock * Product.price)).filter(Product.price.isnot(None)).scalar() or 0
        print(f'\n💰 Total inventory value: ${total_value:,.2f}')
        
        # Count by units
        print(f'\n📊 Products by unit:')
        unit_counts = db.query(Product.unit, func.count(Product.id)).group_by(Product.unit).all()
        for unit, count in unit_counts:
            print(f'   {unit.value}: {count} products')
        
    finally:
        db.close()

if __name__ == "__main__":
    verify_import()

