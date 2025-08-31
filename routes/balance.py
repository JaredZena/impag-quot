from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Balance, BalanceItem, Product, Supplier, SupplierProduct
from auth import verify_google_token

router = APIRouter(prefix="/balance", tags=["balance"])

# Pydantic models
class BalanceItemCreate(BaseModel):
    product_id: int
    supplier_id: int
    quantity: int = 1
    unit_price: float
    shipping_cost: float = 0.0
    notes: Optional[str] = None

class BalanceItemResponse(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_sku: str
    supplier_id: int
    supplier_name: str
    quantity: int
    unit_price: float
    shipping_cost: float
    total_cost: float
    notes: Optional[str]
    
    class Config:
        from_attributes = True

class BalanceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    balance_type: str = "QUOTATION"  # QUOTATION, COMPARISON, ANALYSIS
    currency: str = "MXN"
    items: List[BalanceItemCreate] = []

class BalanceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    balance_type: Optional[str] = None
    is_active: Optional[bool] = None
    items: Optional[List[BalanceItemCreate]] = None

class BalanceResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    balance_type: str
    total_amount: Optional[float]
    currency: str
    is_active: bool
    created_at: datetime
    last_updated: Optional[datetime]
    items: List[BalanceItemResponse] = []
    
    class Config:
        from_attributes = True

class ProductComparisonResponse(BaseModel):
    product_id: int
    product_name: str
    product_sku: str
    suppliers: List[dict]  # List of supplier pricing info

# GET /balance - List all balances
@router.get("/", response_model=List[BalanceResponse])
def get_balances(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=1000),
    search: Optional[str] = Query(None),
    balance_type: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Get all balances with optional filtering"""
    query = db.query(Balance).options(
        joinedload(Balance.items)
        .joinedload(BalanceItem.product),
        joinedload(Balance.items)
        .joinedload(BalanceItem.supplier)
    )
    
    # Filter archived
    if not include_archived:
        query = query.filter(Balance.archived_at.is_(None))
    
    # Filter by active status
    if is_active is not None:
        query = query.filter(Balance.is_active == is_active)
    
    # Filter by balance type
    if balance_type:
        query = query.filter(Balance.balance_type == balance_type)
    
    # Search filter
    if search:
        query = query.filter(
            Balance.name.ilike(f"%{search}%") |
            Balance.description.ilike(f"%{search}%")
        )
    
    # Apply pagination
    balances = query.offset(skip).limit(limit).all()
    
    # Build response
    result = []
    for balance in balances:
        balance_dict = {
            "id": balance.id,
            "name": balance.name,
            "description": balance.description,
            "balance_type": balance.balance_type,
            "total_amount": float(balance.total_amount) if balance.total_amount else None,
            "currency": balance.currency,
            "is_active": balance.is_active,
            "created_at": balance.created_at,
            "last_updated": balance.last_updated,
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "product_name": item.product.name,
                    "product_sku": item.product.sku,
                    "supplier_id": item.supplier_id,
                    "supplier_name": item.supplier.name,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price),
                    "shipping_cost": float(item.shipping_cost),
                    "total_cost": float(item.total_cost),
                    "notes": item.notes
                }
                for item in balance.items
            ]
        }
        result.append(balance_dict)
    
    return result

# GET /balance/{balance_id} - Get single balance
@router.get("/{balance_id}", response_model=BalanceResponse)
def get_balance(balance_id: int, db: Session = Depends(get_db)):
    """Get a single balance by ID"""
    balance = db.query(Balance).options(
        joinedload(Balance.items)
        .joinedload(BalanceItem.product),
        joinedload(Balance.items)
        .joinedload(BalanceItem.supplier)
    ).filter(Balance.id == balance_id).first()
    
    if not balance:
        raise HTTPException(status_code=404, detail="Balance not found")
    
    # Build response
    balance_dict = {
        "id": balance.id,
        "name": balance.name,
        "description": balance.description,
        "balance_type": balance.balance_type,
        "total_amount": float(balance.total_amount) if balance.total_amount else None,
        "currency": balance.currency,
        "is_active": balance.is_active,
        "created_at": balance.created_at,
        "last_updated": balance.last_updated,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product.name,
                "product_sku": item.product.sku,
                "supplier_id": item.supplier_id,
                "supplier_name": item.supplier.name,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price),
                "shipping_cost": float(item.shipping_cost),
                "total_cost": float(item.total_cost),
                "notes": item.notes
            }
            for item in balance.items
        ]
    }
    
    return balance_dict

# POST /balance - Create new balance
@router.post("/", response_model=BalanceResponse)
def create_balance(balance_data: BalanceCreate, db: Session = Depends(get_db)):
    """Create a new balance"""
    
    # Create balance
    db_balance = Balance(
        name=balance_data.name,
        description=balance_data.description,
        balance_type=balance_data.balance_type,
        currency=balance_data.currency
    )
    
    db.add(db_balance)
    db.flush()  # Get the balance ID
    
    total_amount = 0
    
    # Add items
    for item_data in balance_data.items:
        # Verify product exists
        product = db.query(Product).filter(Product.id == item_data.product_id).first()
        if not product:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Product {item_data.product_id} not found")
        
        # Verify supplier exists
        supplier = db.query(Supplier).filter(Supplier.id == item_data.supplier_id).first()
        if not supplier:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Supplier {item_data.supplier_id} not found")
        
        # Calculate total cost
        item_total = (item_data.unit_price + item_data.shipping_cost) * item_data.quantity
        total_amount += item_total
        
        db_item = BalanceItem(
            balance_id=db_balance.id,
            product_id=item_data.product_id,
            supplier_id=item_data.supplier_id,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            shipping_cost=item_data.shipping_cost,
            total_cost=item_total,
            notes=item_data.notes
        )
        db.add(db_item)
    
    # Update total amount
    db_balance.total_amount = total_amount
    
    db.commit()
    
    # Return the created balance
    return get_balance(db_balance.id, db)

# PUT /balance/{balance_id} - Update balance
@router.put("/{balance_id}", response_model=BalanceResponse)
def update_balance(balance_id: int, balance_data: BalanceUpdate, db: Session = Depends(get_db)):
    """Update an existing balance"""
    
    balance = db.query(Balance).filter(Balance.id == balance_id).first()
    if not balance:
        raise HTTPException(status_code=404, detail="Balance not found")
    
    # Update balance fields
    if balance_data.name is not None:
        balance.name = balance_data.name
    if balance_data.description is not None:
        balance.description = balance_data.description
    if balance_data.balance_type is not None:
        balance.balance_type = balance_data.balance_type
    if balance_data.is_active is not None:
        balance.is_active = balance_data.is_active
    
    # Update items if provided
    if balance_data.items is not None:
        # Delete existing items
        db.query(BalanceItem).filter(BalanceItem.balance_id == balance_id).delete()
        
        total_amount = 0
        
        # Add new items
        for item_data in balance_data.items:
            # Verify product exists
            product = db.query(Product).filter(Product.id == item_data.product_id).first()
            if not product:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Product {item_data.product_id} not found")
            
            # Verify supplier exists
            supplier = db.query(Supplier).filter(Supplier.id == item_data.supplier_id).first()
            if not supplier:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Supplier {item_data.supplier_id} not found")
            
            # Calculate total cost
            item_total = (item_data.unit_price + item_data.shipping_cost) * item_data.quantity
            total_amount += item_total
            
            db_item = BalanceItem(
                balance_id=balance_id,
                product_id=item_data.product_id,
                supplier_id=item_data.supplier_id,
                quantity=item_data.quantity,
                unit_price=item_data.unit_price,
                shipping_cost=item_data.shipping_cost,
                total_cost=item_total,
                notes=item_data.notes
            )
            db.add(db_item)
        
        # Update total amount
        balance.total_amount = total_amount
    
    db.commit()
    
    # Return the updated balance
    return get_balance(balance_id, db)

# DELETE /balance/{balance_id} - Archive balance
@router.delete("/{balance_id}")
def archive_balance(balance_id: int, db: Session = Depends(get_db)):
    """Archive a balance (soft delete)"""
    
    balance = db.query(Balance).filter(Balance.id == balance_id).first()
    if not balance:
        raise HTTPException(status_code=404, detail="Balance not found")
    
    balance.archived_at = func.now()
    balance.is_active = False
    db.commit()
    
    return {"message": "Balance archived successfully"}

# GET /balance/compare/{product_id} - Compare suppliers for a product
@router.get("/compare/{product_id}", response_model=ProductComparisonResponse)
def compare_product_suppliers(product_id: int, db: Session = Depends(get_db)):
    """Compare all suppliers that offer a specific product"""
    
    # Verify product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Get all supplier products for this product
    supplier_products = db.query(SupplierProduct).options(
        joinedload(SupplierProduct.supplier)
    ).filter(
        SupplierProduct.product_id == product_id,
        SupplierProduct.is_active == True
    ).all()
    
    # Build supplier comparison
    suppliers_info = []
    for sp in supplier_products:
        # Calculate total shipping cost based on method
        if sp.shipping_method == 'DIRECT':
            total_shipping = float(sp.shipping_cost_direct or 0)
        else:
            total_shipping = float(
                (sp.shipping_stage1_cost or 0) +
                (sp.shipping_stage2_cost or 0) +
                (sp.shipping_stage3_cost or 0) +
                (sp.shipping_stage4_cost or 0)
            )
        
        total_unit_cost = float(sp.cost or 0) + total_shipping
        
        suppliers_info.append({
            "supplier_id": sp.supplier_id,
            "supplier_name": sp.supplier.name,
            "unit_cost": float(sp.cost or 0),
            "shipping_cost": total_shipping,
            "total_unit_cost": total_unit_cost,
            "shipping_method": sp.shipping_method,
            "stock": sp.stock,
            "lead_time_days": sp.lead_time_days,
            "supplier_sku": sp.supplier_sku
        })
    
    # Sort by total cost
    suppliers_info.sort(key=lambda x: x["total_unit_cost"])
    
    return {
        "product_id": product.id,
        "product_name": product.name,
        "product_sku": product.sku,
        "suppliers": suppliers_info
    }

# POST /balance/quick-compare - Quick comparison tool
@router.post("/quick-compare")
def quick_compare_products(
    product_ids: List[int],
    db: Session = Depends(get_db)
):
    """Quick comparison of multiple products showing best suppliers"""
    
    results = []
    
    for product_id in product_ids:
        try:
            comparison = compare_product_suppliers(product_id, db)
            results.append({
                "product_id": product_id,
                "product_name": comparison["product_name"],
                "product_sku": comparison["product_sku"],
                "best_supplier": comparison["suppliers"][0] if comparison["suppliers"] else None,
                "total_suppliers": len(comparison["suppliers"])
            })
        except HTTPException:
            results.append({
                "product_id": product_id,
                "error": "Product not found or no suppliers available"
            })
    
    return {"comparisons": results}