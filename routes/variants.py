from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Product, ProductVariant, SupplierProduct, Supplier

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

# GET /products/{product_id}/variants
@router.get("/products/{product_id}/variants")
def get_variants_for_product(product_id: int, db: Session = Depends(get_db)):
    variants = db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all()
    data = [
        {
            "id": v.id,
            "product_id": v.product_id,
            "sku": v.sku,
            "price": float(v.price) if v.price is not None else None,
            "stock": v.stock,
            "specifications": v.specifications,
            "is_active": v.is_active,
            "created_at": v.created_at,
            "last_updated": v.last_updated,
        }
        for v in variants
    ]
    return {"success": True, "data": data, "error": None, "message": None}

# GET /variants/{variant_id}
@router.get("/{variant_id}")
def get_variant(variant_id: int, db: Session = Depends(get_db)):
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

# POST /products/{product_id}/variants
@router.post("/products/{product_id}/variants")
def create_variant(product_id: int, variant: VariantCreate, db: Session = Depends(get_db)):
    # Check product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    # Check for duplicate SKU
    existing = db.query(ProductVariant).filter(ProductVariant.sku == variant.sku).first()
    if existing:
        return {"success": False, "data": None, "error": "Variant with this SKU already exists", "message": None}
    new_variant = ProductVariant(
        product_id=product_id,
        sku=variant.sku,
        price=variant.price,
        stock=variant.stock,
        specifications=variant.specifications,
        is_active=variant.is_active
    )
    db.add(new_variant)
    db.commit()
    db.refresh(new_variant)
    data = {
        "id": new_variant.id,
        "product_id": new_variant.product_id,
        "sku": new_variant.sku,
        "price": float(new_variant.price) if new_variant.price is not None else None,
        "stock": new_variant.stock,
        "specifications": new_variant.specifications,
        "is_active": new_variant.is_active,
        "created_at": new_variant.created_at,
        "last_updated": new_variant.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# PUT /variants/{variant_id}
@router.put("/{variant_id}")
def update_variant(variant_id: int, variant: VariantUpdate, db: Session = Depends(get_db)):
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
def get_suppliers_for_variant(variant_id: int, db: Session = Depends(get_db)):
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