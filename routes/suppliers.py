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
    common_name: Optional[str] = None  # Common name or trading name of the supplier
    legal_name: Optional[str] = None  # Legal/registered name of the supplier
    rfc: str  # RFC is required
    description: Optional[str] = None
    contact_name: Optional[str] = None  # Full name of the contact person
    contact_common_name: Optional[str] = None  # Common name/nickname of the contact person
    contact_info: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class SupplierCreate(SupplierBase):
    pass

class Supplier(SupplierBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Supplier endpoints
<<<<<<< HEAD
@router.post("/", response_model=Supplier)
def create_supplier(supplier: SupplierCreate, db: Session = Depends(get_db)):
=======
@router.post("/")
def create_supplier(supplier: SupplierCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    db_supplier = Supplier(**supplier.model_dump())
    db.add(db_supplier)
    db.commit()
    db.refresh(db_supplier)
    return db_supplier

<<<<<<< HEAD
@router.get("/", response_model=List[Supplier])
def get_suppliers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    suppliers = db.query(Supplier).offset(skip).limit(limit).all()
    return suppliers

@router.get("/{supplier_id}", response_model=Supplier)
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
=======
@router.get("/")
def get_suppliers(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    query = db.query(Supplier)
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
            "created_at": s.created_at,
            "last_updated": s.last_updated,
        }
        for s in suppliers
    ]
    return {"success": True, "data": data, "error": None, "message": None}

@router.get("/{supplier_id}")
def get_supplier(supplier_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier

<<<<<<< HEAD
@router.put("/{supplier_id}", response_model=Supplier)
def update_supplier(supplier_id: int, supplier: SupplierCreate, db: Session = Depends(get_db)):
=======
@router.put("/{supplier_id}")
def update_supplier(supplier_id: int, supplier: SupplierCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    db_supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if db_supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    
    for key, value in supplier.model_dump().items():
        setattr(db_supplier, key, value)
    
    db.commit()
    db.refresh(db_supplier)
    return db_supplier 