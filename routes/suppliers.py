from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Supplier, SupplierProduct, Product
from auth import verify_google_token

router = APIRouter(prefix="/suppliers", tags=["suppliers"])

# Pydantic models for request/response
class SupplierBase(BaseModel):
    name: str
    common_name: Optional[str] = None
    legal_name: Optional[str] = None
    rfc: str
    description: Optional[str] = None
    contact_name: Optional[str] = None
    contact_common_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website_url: Optional[str] = None

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    common_name: Optional[str] = None
    legal_name: Optional[str] = None
    rfc: Optional[str] = None
    description: Optional[str] = None
    contact_name: Optional[str] = None
    contact_common_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website_url: Optional[str] = None
    archived_at: Optional[datetime] = None

class SupplierCreate(SupplierBase):
    pass

class SupplierResponse(SupplierBase):
    id: int
    archived_at: Optional[datetime] = None
    created_at: datetime
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

# Supplier endpoints
# POST /suppliers - REQUIRES AUTHENTICATION for admin operations
@router.post("/")
@router.post("")  # Handle both /suppliers and /suppliers/ explicitly
def create_supplier(supplier: SupplierCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    db_supplier = Supplier(**supplier.model_dump())
    db.add(db_supplier)
    db.commit()
    db.refresh(db_supplier)
    data = {
        "id": db_supplier.id,
        "name": db_supplier.name,
        "common_name": db_supplier.common_name,
        "legal_name": db_supplier.legal_name,
        "rfc": db_supplier.rfc,
        "description": db_supplier.description,
        "contact_name": db_supplier.contact_name,
        "contact_common_name": db_supplier.contact_common_name,
        "email": db_supplier.email,
        "phone": db_supplier.phone,
        "address": db_supplier.address,
        "website_url": db_supplier.website_url,
        "archived_at": db_supplier.archived_at,
        "created_at": db_supplier.created_at,
        "last_updated": db_supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# GET /suppliers - PUBLIC for quotation web app
@router.get("/")
@router.get("")  # Handle both /suppliers and /suppliers/ explicitly
def get_suppliers(
    search: Optional[str] = None,
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 100,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "asc",
    db: Session = Depends(get_db)
):
    query = db.query(Supplier)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(Supplier.archived_at.is_(None))
    
    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            (Supplier.name.ilike(like_pattern)) |
            (Supplier.contact_name.ilike(like_pattern)) |
            (Supplier.email.ilike(like_pattern))
        )
    
    # Add sorting - default sort by name if no sort_by provided
    if not sort_by:
        sort_by = "name"
        
    if sort_by == "name":
        query = query.order_by(Supplier.name.asc() if sort_order == "asc" else Supplier.name.desc())
    elif sort_by == "created_at":
        query = query.order_by(Supplier.created_at.asc() if sort_order == "asc" else Supplier.created_at.desc())
    elif sort_by == "last_updated":
        query = query.order_by(Supplier.last_updated.asc() if sort_order == "asc" else Supplier.last_updated.desc())
    else:
        # Default fallback to name sorting
        query = query.order_by(Supplier.name.asc())
        
    suppliers = query.offset(skip).limit(limit).all()
    data = [
        {
            "id": s.id,
            "name": s.name,
            "common_name": s.common_name,
            "legal_name": s.legal_name,
            "rfc": s.rfc,
            "description": s.description,
            "contact_name": s.contact_name,
            "contact_common_name": s.contact_common_name,
            "email": s.email,
            "phone": s.phone,
            "address": s.address,
            "website_url": s.website_url,
            "archived_at": s.archived_at,
            "created_at": s.created_at,
            "last_updated": s.last_updated,
        }
        for s in suppliers
    ]
    return {"success": True, "data": data, "error": None, "message": None}

# GET /suppliers/{supplier_id} - PUBLIC for quotation web app
@router.get("/{supplier_id}")
def get_supplier(supplier_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    query = db.query(Supplier).filter(Supplier.id == supplier_id)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(Supplier.archived_at.is_(None))
        
    supplier = query.first()
    if supplier is None:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    data = {
        "id": supplier.id,
        "name": supplier.name,
        "common_name": supplier.common_name,
        "legal_name": supplier.legal_name,
        "rfc": supplier.rfc,
        "description": supplier.description,
        "contact_name": supplier.contact_name,
        "contact_common_name": supplier.contact_common_name,
        "email": supplier.email,
        "phone": supplier.phone,
        "address": supplier.address,
        "website_url": supplier.website_url,
        "archived_at": supplier.archived_at,
        "created_at": supplier.created_at,
        "last_updated": supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# PUT /suppliers/{supplier_id} - REQUIRES AUTHENTICATION for admin operations
@router.put("/{supplier_id}")
def update_supplier(supplier_id: int, supplier: SupplierUpdate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    db_supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if db_supplier is None:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    for key, value in supplier.model_dump(exclude_unset=True).items():
        setattr(db_supplier, key, value)
    db.commit()
    db.refresh(db_supplier)
    data = {
        "id": db_supplier.id,
        "name": db_supplier.name,
        "common_name": db_supplier.common_name,
        "legal_name": db_supplier.legal_name,
        "rfc": db_supplier.rfc,
        "description": db_supplier.description,
        "contact_name": db_supplier.contact_name,
        "contact_common_name": db_supplier.contact_common_name,
        "email": db_supplier.email,
        "phone": db_supplier.phone,
        "address": db_supplier.address,
        "website_url": db_supplier.website_url,
        "archived_at": db_supplier.archived_at,
        "created_at": db_supplier.created_at,
        "last_updated": db_supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None} 
# Archive/Unarchive endpoints
@router.patch("/{supplier_id}/archive")
def archive_supplier(supplier_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Archive a supplier (soft delete)"""
    db_supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if db_supplier is None:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    
    db_supplier.archived_at = datetime.utcnow()
    db.commit()
    db.refresh(db_supplier)
    
    return {"success": True, "data": {"id": supplier_id, "archived_at": db_supplier.archived_at}, "error": None, "message": "Supplier archived successfully"}

@router.patch("/{supplier_id}/unarchive")
def unarchive_supplier(supplier_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Unarchive a supplier (restore from soft delete)"""
    db_supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if db_supplier is None:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    
    db_supplier.archived_at = None
    db.commit()
    db.refresh(db_supplier)
    
    return {"success": True, "data": {"id": supplier_id, "archived_at": None}, "error": None, "message": "Supplier restored successfully"}

@router.get("/{supplier_id}/products")
def get_supplier_products(supplier_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    """Get all products supplied by a specific supplier with pricing and relationship details"""
    # Verify supplier exists
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if not supplier:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    
    # Query supplier-product relationships for this supplier
    query = db.query(SupplierProduct).filter(SupplierProduct.supplier_id == supplier_id)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(SupplierProduct.archived_at.is_(None))
    
    supplier_products = query.all()
    
    if not supplier_products:
        return {"success": True, "data": [], "error": None, "message": None}
    
    # Get product details and combine with supplier-specific info
    products_with_supplier_info = []
    for sp in supplier_products:
        # Get the product details
        product = db.query(Product).filter(Product.id == sp.product_id).first()
        if product and (include_archived or product.archived_at is None):
            product_data = {
                # Supplier-specific information
                "supplier_product_id": sp.id,
                "supplier_sku": sp.supplier_sku,
                "cost": float(sp.cost) if sp.cost is not None else None,
                "stock": sp.stock,
                "lead_time_days": sp.lead_time_days,
                "supplier_is_active": sp.is_active,
                "supplier_notes": sp.notes,
                "supplier_relationship_created_at": sp.created_at,
                "supplier_relationship_last_updated": sp.last_updated,
                
                # Product information
                "product_id": product.id,
                "product_name": product.name,
                "product_description": product.description,
                "category_id": product.category_id,
                "base_sku": product.base_sku,
                "sku": product.sku,
                "iva": product.iva,
                "unit": product.unit.value if product.unit else None,
                "package_size": product.package_size,
                "base_price": float(product.price) if product.price is not None else None,
                "base_stock": product.stock,
                "specifications": product.specifications,
                "product_is_active": product.is_active,
                "product_created_at": product.created_at,
                "product_last_updated": product.last_updated,
            }
            products_with_supplier_info.append(product_data)
    
    return {"success": True, "data": products_with_supplier_info, "error": None, "message": None}
