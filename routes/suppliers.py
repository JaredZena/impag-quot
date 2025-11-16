from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case
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
        # Normalize spaces for better fuzzy matching
        normalized_search = func.regexp_replace(func.unaccent(search), r'\s+', '', 'g')
        # Use COALESCE to handle NULL values (default to empty string for normalization)
        normalized_name = func.regexp_replace(func.unaccent(func.coalesce(Supplier.name, '')), r'\s+', '', 'g')
        normalized_common_name = func.regexp_replace(func.unaccent(func.coalesce(Supplier.common_name, '')), r'\s+', '', 'g')
        normalized_legal_name = func.regexp_replace(func.unaccent(func.coalesce(Supplier.legal_name, '')), r'\s+', '', 'g')
        normalized_contact = func.regexp_replace(func.unaccent(func.coalesce(Supplier.contact_name, '')), r'\s+', '', 'g')
        
        # Exact matches (handle NULL with COALESCE)
        name_exact = func.unaccent(func.coalesce(Supplier.name, '')).ilike(func.unaccent(f"%{search}%"))
        common_name_exact = func.unaccent(func.coalesce(Supplier.common_name, '')).ilike(func.unaccent(f"%{search}%"))
        legal_name_exact = func.unaccent(func.coalesce(Supplier.legal_name, '')).ilike(func.unaccent(f"%{search}%"))
        contact_exact = func.unaccent(func.coalesce(Supplier.contact_name, '')).ilike(func.unaccent(f"%{search}%"))
        email_exact = func.coalesce(Supplier.email, '').ilike(f"%{search}%")
        
        # Fuzzy matches with space normalization
        name_fuzzy = func.similarity(normalized_name, normalized_search) > 0.2
        common_name_fuzzy = func.similarity(normalized_common_name, normalized_search) > 0.2
        legal_name_fuzzy = func.similarity(normalized_legal_name, normalized_search) > 0.2
        contact_fuzzy = func.similarity(normalized_contact, normalized_search) > 0.2
        
        # Word similarity matches
        name_word = func.word_similarity(normalized_search, normalized_name) > 0.2
        common_name_word = func.word_similarity(normalized_search, normalized_common_name) > 0.2
        legal_name_word = func.word_similarity(normalized_search, normalized_legal_name) > 0.2
        contact_word = func.word_similarity(normalized_search, normalized_contact) > 0.2
        
        query = query.filter(
            name_exact | common_name_exact | legal_name_exact | contact_exact | email_exact | 
            name_fuzzy | common_name_fuzzy | legal_name_fuzzy | contact_fuzzy | 
            name_word | common_name_word | legal_name_word | contact_word
        )
        
        # Order by best similarity score
        name_similarity = func.similarity(normalized_name, normalized_search)
        common_name_similarity = func.similarity(normalized_common_name, normalized_search)
        legal_name_similarity = func.similarity(normalized_legal_name, normalized_search)
        contact_similarity = func.similarity(normalized_contact, normalized_search)
        name_word_sim = func.word_similarity(normalized_search, normalized_name)
        common_name_word_sim = func.word_similarity(normalized_search, normalized_common_name)
        legal_name_word_sim = func.word_similarity(normalized_search, normalized_legal_name)
        contact_word_sim = func.word_similarity(normalized_search, normalized_contact)
        
        best_score = func.greatest(
            name_similarity, common_name_similarity, legal_name_similarity, contact_similarity,
            name_word_sim, common_name_word_sim, legal_name_word_sim, contact_word_sim
        )
        
        exact_score = case(
            (name_exact | common_name_exact | legal_name_exact | contact_exact | email_exact, 1.0),
            else_=best_score
        )
        query = query.order_by(exact_score.desc())
    
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
    
    # Get product details directly from supplier_product (no Product table query needed)
    products_with_supplier_info = []
    for sp in supplier_products:
        # Check if archived (using supplier_product archived_at)
        if include_archived or sp.archived_at is None:
            product_data = {
                # Supplier-specific information
                "supplier_product_id": sp.id,
                "supplier_sku": sp.supplier_sku,
                "cost": float(sp.cost) if sp.cost is not None else None,
                "currency": sp.currency if sp.currency is not None else 'MXN',
                "stock": sp.stock,
                "lead_time_days": sp.lead_time_days,
                "supplier_is_active": sp.is_active,
                "supplier_notes": sp.notes,
                "supplier_relationship_created_at": sp.created_at,
                "supplier_relationship_last_updated": sp.last_updated,
                # Add aliases for frontend compatibility
                "created_at": sp.created_at,
                "last_updated": sp.last_updated,
                
                # Product information (now from SupplierProduct directly)
                "product_id": sp.product_id,  # Keep for backward compatibility
                "product_name": sp.name,
                "product_description": sp.description,
                "category_id": sp.category_id,
                "base_sku": sp.base_sku,
                "sku": sp.sku,
                "iva": sp.iva,
                "unit": sp.unit,  # Already a string in SupplierProduct
                "package_size": sp.package_size,
                "base_price": float(sp.cost) if sp.cost is not None else None,  # Use cost as base_price
                "base_stock": sp.stock,  # Use supplier_product stock
                "specifications": sp.specifications,
                "product_is_active": sp.is_active,  # Use supplier_product is_active
                "product_created_at": sp.created_at,
                "product_last_updated": sp.last_updated,
            }
            products_with_supplier_info.append(product_data)
    
    return {"success": True, "data": products_with_supplier_info, "error": None, "message": None}
