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

class SupplierCreate(SupplierBase):
    pass

class SupplierResponse(SupplierBase):
    id: int
    created_at: datetime
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

# Supplier endpoints
<<<<<<< HEAD
<<<<<<< HEAD
@router.post("/", response_model=Supplier)
=======
@router.post("/")
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50
def create_supplier(supplier: SupplierCreate, db: Session = Depends(get_db)):
=======
@router.post("/")
def create_supplier(supplier: SupplierCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
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
        "created_at": db_supplier.created_at,
        "last_updated": db_supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

<<<<<<< HEAD
<<<<<<< HEAD
@router.get("/", response_model=List[Supplier])
def get_suppliers(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    suppliers = db.query(Supplier).offset(skip).limit(limit).all()
    return suppliers
=======
@router.get("/")
def get_suppliers(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
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
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50

@router.get("/{supplier_id}")
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
        "created_at": supplier.created_at,
        "last_updated": supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

<<<<<<< HEAD
<<<<<<< HEAD
@router.put("/{supplier_id}", response_model=Supplier)
=======
@router.put("/{supplier_id}")
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50
def update_supplier(supplier_id: int, supplier: SupplierCreate, db: Session = Depends(get_db)):
=======
@router.put("/{supplier_id}")
def update_supplier(supplier_id: int, supplier: SupplierCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    db_supplier = db.query(Supplier).filter(Supplier.id == supplier_id).first()
    if db_supplier is None:
        return {"success": False, "data": None, "error": "Supplier not found", "message": None}
    for key, value in supplier.model_dump().items():
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
        "created_at": db_supplier.created_at,
        "last_updated": db_supplier.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None} 