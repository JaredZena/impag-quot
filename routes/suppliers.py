from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Supplier
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
def get_suppliers(
    search: Optional[str] = None,
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 100,
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
