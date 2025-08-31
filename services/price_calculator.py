"""
Price calculation service for products with default margin
"""

from decimal import Decimal
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from models import Product, SupplierProduct
from sqlalchemy import and_


def calculate_price_with_margin(cost: Decimal, margin: Decimal) -> Decimal:
    """
    Calculate price from cost and margin percentage
    
    Args:
        cost: The supplier cost
        margin: The margin as a decimal (0.25 = 25%)
    
    Returns:
        The calculated price rounded to 2 decimal places
    """
    if not cost or not margin:
        return None
    
    # Price = Cost / (1 - margin)
    # This ensures the margin is applied correctly
    # Example: cost=100, margin=0.25 -> price = 100 / 0.75 = 133.33
    # Profit = 133.33 - 100 = 33.33 (25% of final price)
    return round(cost / (Decimal('1') - margin), 2)


def get_lowest_supplier_cost(product_id: int, db: Session) -> Optional[Decimal]:
    """
    Get the lowest total cost (base cost + shipping) from all active suppliers for a product
    
    Args:
        product_id: The product ID
        db: Database session
    
    Returns:
        The lowest total supplier cost (including shipping) or None if no suppliers with cost
    """
    # Query for cost + total shipping cost based on shipping method
    from sqlalchemy import func, case
    
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
    
    supplier_costs = db.query(
        (SupplierProduct.cost + total_shipping_cost).label('total_cost')
    ).filter(
        and_(
            SupplierProduct.product_id == product_id,
            SupplierProduct.is_active == True,
            SupplierProduct.cost.isnot(None),
            SupplierProduct.cost > 0
        )
    ).order_by((SupplierProduct.cost + total_shipping_cost).asc()).first()
    
    return supplier_costs[0] if supplier_costs else None


def calculate_product_price_with_default_margin(product: Product, db: Session) -> Optional[Decimal]:
    """
    Calculate product price using default margin if price is null
    
    Args:
        product: The Product object
        db: Database session
    
    Returns:
        Calculated price or None if calculation not possible
    """
    # If product already has a price, return it
    if product.price is not None:
        return product.price
    
    # If no default margin, can't calculate
    if product.default_margin is None:
        return None
    
    # Get lowest supplier cost
    lowest_cost = get_lowest_supplier_cost(product.id, db)
    if lowest_cost is None:
        return None
    
    # Calculate price with margin
    return calculate_price_with_margin(lowest_cost, product.default_margin)


def enrich_products_with_calculated_prices(products: List[Dict[str, Any]], db: Session) -> List[Dict[str, Any]]:
    """
    Enrich a list of product dictionaries with calculated prices where needed
    
    Args:
        products: List of product dictionaries from database query
        db: Database session
    
    Returns:
        List of products with calculated_price field added and price field updated if needed
    """
    if not products:
        return products
    
    # Performance optimization: get all product IDs that need price calculation
    products_needing_calculation = [
        p for p in products 
        if p.get('price') is None and p.get('default_margin') is not None
    ]
    
    if not products_needing_calculation:
        # No products need calculation, just add the fields
        for product in products:
            product['calculated_price'] = None
            product['is_calculated_price'] = False
        return products
    
    # Batch query for supplier costs for all products that need calculation
    product_ids = [p['id'] for p in products_needing_calculation]
    
    # Get lowest total costs (base cost + shipping) for all products in one query
    from sqlalchemy import and_, func, case
    
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
    
    lowest_costs_query = db.query(
        SupplierProduct.product_id,
        func.min(SupplierProduct.cost + total_shipping_cost).label('lowest_cost')
    ).filter(
        and_(
            SupplierProduct.product_id.in_(product_ids),
            SupplierProduct.is_active == True,
            SupplierProduct.cost.isnot(None),
            SupplierProduct.cost > 0
        )
    ).group_by(SupplierProduct.product_id).all()
    
    # Create a mapping of product_id -> lowest_cost
    costs_map = {row.product_id: row.lowest_cost for row in lowest_costs_query}
    
    enriched_products = []
    
    for product_data in products:
        enriched_product = product_data.copy()
        
        # If price is null and we have default_margin, try to calculate
        if (product_data.get('price') is None and 
            product_data.get('default_margin') is not None):
            
            lowest_cost = costs_map.get(product_data['id'])
            if lowest_cost is not None:
                calculated_price = calculate_price_with_margin(
                    lowest_cost, 
                    Decimal(str(product_data['default_margin']))
                )
                if calculated_price is not None:
                    enriched_product['calculated_price'] = float(calculated_price)
                    enriched_product['price'] = float(calculated_price)  # Use calculated price as display price
                    enriched_product['is_calculated_price'] = True
                else:
                    enriched_product['calculated_price'] = None
                    enriched_product['is_calculated_price'] = False
            else:
                enriched_product['calculated_price'] = None
                enriched_product['is_calculated_price'] = False
        else:
            enriched_product['calculated_price'] = None
            enriched_product['is_calculated_price'] = False
        
        enriched_products.append(enriched_product)
    
    return enriched_products


def get_product_display_price(product_id: int, db: Session) -> Dict[str, Any]:
    """
    Get the display price for a product (either real price or calculated price)
    
    Args:
        product_id: The product ID
        db: Database session
    
    Returns:
        Dictionary with price information
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return {
            'price': None,
            'calculated_price': None,
            'is_calculated': False,
            'lowest_supplier_cost': None,
            'margin_used': None
        }
    
    result = {
        'price': float(product.price) if product.price else None,
        'calculated_price': None,
        'is_calculated': False,
        'lowest_supplier_cost': None,
        'margin_used': float(product.default_margin) if product.default_margin else None
    }
    
    # If no price set, try to calculate
    if product.price is None and product.default_margin is not None:
        lowest_cost = get_lowest_supplier_cost(product_id, db)
        if lowest_cost is not None:
            calculated_price = calculate_price_with_margin(lowest_cost, product.default_margin)
            if calculated_price is not None:
                result['calculated_price'] = float(calculated_price)
                result['price'] = float(calculated_price)  # Use as display price
                result['is_calculated'] = True
                result['lowest_supplier_cost'] = float(lowest_cost)
    
    return result
