from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Product, ProductVariant, SupplierProduct, Supplier
from auth import verify_google_token

router = APIRouter(prefix="/variants", tags=["variants"])

# Pydantic models
class VariantBase(BaseModel):
    sku: str
    price: Optional[float] = None
    stock: Optional[int] = 0
    specifications: Optional[Any] = None
    is_active: Optional[bool] = True

class VariantCreate(VariantBase):
    pass

class VariantUpdate(BaseModel):
    price: Optional[float] = None
    stock: Optional[int] = None
    specifications: Optional[Any] = None
    is_active: Optional[bool] = None

class SupplierInfo(BaseModel):
    id: int
    name: str
    is_active: bool
    cost: Optional[float] = None
    stock: Optional[int] = None
    lead_time_days: Optional[int] = None
    notes: Optional[str] = None

class VariantResponse(VariantBase):
    id: int
    product_id: int
    created_at: datetime
    last_updated: Optional[datetime] = None
    suppliers: Optional[List[SupplierInfo]] = []

    class Config:
        orm_mode = True

# Moved to /products/{product_id}/variants for better REST design

# GET /variants/{variant_id}
@router.get("/{variant_id}")
def get_variant(variant_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        return {"success": False, "data": None, "error": "Variant not found", "message": None}
    # Get suppliers for this variant
    supplier_products = db.query(SupplierProduct).filter(SupplierProduct.variant_id == variant_id).all()
    suppliers = []
    for sp in supplier_products:
        supplier = db.query(Supplier).filter(Supplier.id == sp.supplier_id).first()
        if supplier:
            suppliers.append({
                "id": supplier.id,
                "name": supplier.name,
                "is_active": sp.is_active,
                "cost": float(sp.cost) if sp.cost is not None else None,
                "stock": sp.stock,
                "lead_time_days": sp.lead_time_days,
                "notes": sp.notes,
            })
    data = {
        "id": variant.id,
        "product_id": variant.product_id,
        "sku": variant.sku,
        "price": float(variant.price) if variant.price is not None else None,
        "stock": variant.stock,
        "specifications": variant.specifications,
        "is_active": variant.is_active,
        "created_at": variant.created_at,
        "last_updated": variant.last_updated,
        "suppliers": suppliers,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# Moved to /products/{product_id}/variants for better REST design

# PUT /variants/{variant_id}
@router.put("/{variant_id}")
def update_variant(variant_id: int, variant: VariantUpdate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    db_variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not db_variant:
        return {"success": False, "data": None, "error": "Variant not found", "message": None}
    for key, value in variant.model_dump(exclude_unset=True).items():
        setattr(db_variant, key, value)
    db.commit()
    db.refresh(db_variant)
    data = {
        "id": db_variant.id,
        "product_id": db_variant.product_id,
        "sku": db_variant.sku,
        "price": float(db_variant.price) if db_variant.price is not None else None,
        "stock": db_variant.stock,
        "specifications": db_variant.specifications,
        "is_active": db_variant.is_active,
        "created_at": db_variant.created_at,
        "last_updated": db_variant.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# GET /variants/{variant_id}/suppliers
@router.get("/{variant_id}/suppliers")
def get_suppliers_for_variant(variant_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    supplier_products = db.query(SupplierProduct).filter(SupplierProduct.variant_id == variant_id).all()
    suppliers = []
    for sp in supplier_products:
        supplier = db.query(Supplier).filter(Supplier.id == sp.supplier_id).first()
        if supplier:
            suppliers.append({
                "id": supplier.id,
                "name": supplier.name,
                "is_active": sp.is_active,
                "cost": float(sp.cost) if sp.cost is not None else None,
                "stock": sp.stock,
                "lead_time_days": sp.lead_time_days,
                "notes": sp.notes,
            })
    return {"success": True, "data": suppliers, "error": None, "message": None} 