from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Product, Supplier, SupplierProduct, ProductUnit
from auth import verify_google_token

router = APIRouter(prefix="/products", tags=["products"])

# Pydantic models for request/response
class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    category_id: Optional[int] = None
    base_sku: Optional[str] = None
    iva: Optional[bool] = True
    unit: Optional[ProductUnit] = ProductUnit.PIEZA
    package_size: Optional[int] = None
    # New fields from flattened variant
    sku: str
    price: Optional[float] = None
    stock: Optional[int] = 0
    specifications: Optional[Any] = None
    is_active: Optional[bool] = True

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    base_sku: Optional[str] = None
    iva: Optional[bool] = None
    unit: Optional[ProductUnit] = None
    package_size: Optional[int] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    specifications: Optional[Any] = None
    is_active: Optional[bool] = None
    archived_at: Optional[datetime] = None

class ProductResponse(ProductBase):
    id: int
    archived_at: Optional[datetime] = None
    created_at: datetime
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

class SupplierProductBase(BaseModel):
    supplier_id: int
    product_id: int
    supplier_sku: Optional[str] = None
    cost: Optional[float] = None
    stock: Optional[int] = 0
    lead_time_days: Optional[int] = None
    is_active: Optional[bool] = True
    notes: Optional[str] = None

class SupplierProductCreate(SupplierProductBase):
    pass

class SupplierProductUpdate(BaseModel):
    supplier_id: Optional[int] = None
    product_id: Optional[int] = None
    supplier_sku: Optional[str] = None
    cost: Optional[float] = None
    stock: Optional[int] = None
    lead_time_days: Optional[int] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    archived_at: Optional[datetime] = None

class SupplierProductResponse(SupplierProductBase):
    id: int
    archived_at: Optional[datetime] = None
    created_at: datetime
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

# GET /products with advanced filtering and JSON wrapper - PUBLIC for quotation web app
@router.get("/")
@router.get("")  # Handle both /products and /products/ explicitly
def get_products(
    id: Optional[int] = Query(None),
    name: Optional[str] = Query(None),
    sku: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 100,
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query("asc"),
    db: Session = Depends(get_db)
):
    query = db.query(Product)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(Product.archived_at.is_(None))
    if id:
        query = query.filter(Product.id == id)
    if name:
        query = query.filter(Product.name.ilike(f"%{name}%"))
    if sku:
        like_pattern = f"%{sku}%"
        # Filter by either base_sku or sku
        query = query.filter(
            (Product.base_sku.ilike(like_pattern)) |
            (Product.sku.ilike(like_pattern))
        )
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if is_active is not None:
        query = query.filter(Product.is_active == is_active)
    if supplier_id:
        query = query.join(Product.supplier_products).filter(SupplierProduct.supplier_id == supplier_id)
    
    # Add sorting
    if sort_by == "name" and sort_order == "asc":
        query = query.order_by(Product.name.asc())
    elif sort_by == "name" and sort_order == "desc":
        query = query.order_by(Product.name.desc())
    
    products = query.offset(skip).limit(limit).all()
    data = [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "category_id": p.category_id,
            "base_sku": p.base_sku,
            "iva": p.iva,
            "unit": p.unit.value if p.unit else None,
            "package_size": p.package_size,
            "sku": p.sku,
            "price": float(p.price) if p.price is not None else None,
            "stock": p.stock,
            "specifications": p.specifications,
            "is_active": p.is_active,
            "archived_at": p.archived_at,
            "created_at": p.created_at,
            "last_updated": p.last_updated,
        }
        for p in products
    ]
    return {"success": True, "data": data, "error": None, "message": None}

# GET /products/{product_id} - PUBLIC for quotation web app
@router.get("/{product_id}")
def get_product(product_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    query = db.query(Product).filter(Product.id == product_id)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(Product.archived_at.is_(None))
        
    product = query.first()
    if product is None:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    data = {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "category_id": product.category_id,
        "base_sku": product.base_sku,
        "iva": product.iva,
        "unit": product.unit.value if product.unit else None,
        "package_size": product.package_size,
        "sku": product.sku,
        "price": float(product.price) if product.price is not None else None,
        "stock": product.stock,
        "specifications": product.specifications,
        "is_active": product.is_active,
        "archived_at": product.archived_at,
        "created_at": product.created_at,
        "last_updated": product.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# POST /products - REQUIRES AUTHENTICATION for admin operations
@router.post("/")
@router.post("")  # Handle both /products and /products/ explicitly
def create_product(product: ProductCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    # Check for duplicate SKU
    existing = db.query(Product).filter(Product.sku == product.sku).first()
    if existing:
        return {"success": False, "data": None, "error": "Product with this SKU already exists", "message": None}
    
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    
    data = {
        "id": db_product.id,
        "name": db_product.name,
        "description": db_product.description,
        "category_id": db_product.category_id,
        "base_sku": db_product.base_sku,
        "iva": db_product.iva,
        "unit": db_product.unit.value if db_product.unit else None,
        "package_size": db_product.package_size,
        "sku": db_product.sku,
        "price": float(db_product.price) if db_product.price is not None else None,
        "stock": db_product.stock,
        "specifications": db_product.specifications,
        "is_active": db_product.is_active,
        "archived_at": db_product.archived_at,
        "created_at": db_product.created_at,
        "last_updated": db_product.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# PUT /products/{product_id} - REQUIRES AUTHENTICATION for admin operations
@router.put("/{product_id}")
def update_product(product_id: int, product: ProductUpdate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    
    # Check for duplicate SKU if sku is being updated
    if product.sku and product.sku != db_product.sku:
        existing = db.query(Product).filter(Product.sku == product.sku).first()
        if existing:
            return {"success": False, "data": None, "error": "Product with this SKU already exists", "message": None}
    
    for key, value in product.model_dump(exclude_unset=True).items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    
    data = {
        "id": db_product.id,
        "name": db_product.name,
        "description": db_product.description,
        "category_id": db_product.category_id,
        "base_sku": db_product.base_sku,
        "iva": db_product.iva,
        "unit": db_product.unit.value if db_product.unit else None,
        "package_size": db_product.package_size,
        "sku": db_product.sku,
        "price": float(db_product.price) if db_product.price is not None else None,
        "stock": db_product.stock,
        "specifications": db_product.specifications,
        "is_active": db_product.is_active,
        "archived_at": db_product.archived_at,
        "created_at": db_product.created_at,
        "last_updated": db_product.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# SupplierProduct endpoints - ALL REQUIRE AUTHENTICATION for admin operations
@router.post("/supplier-product/", response_model=SupplierProductResponse)
def create_supplier_product(supplier_product: SupplierProductCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    # Verify supplier and product exist
    supplier = db.query(Supplier).filter(Supplier.id == supplier_product.supplier_id).first()
    product = db.query(Product).filter(Product.id == supplier_product.product_id).first()
    
    if not supplier or not product:
        raise HTTPException(status_code=404, detail="Supplier or Product not found")
    
    db_supplier_product = SupplierProduct(**supplier_product.model_dump())
    db.add(db_supplier_product)
    db.commit()
    db.refresh(db_supplier_product)
    return db_supplier_product

@router.get("/supplier-product/debug")
def debug_supplier_products(db: Session = Depends(get_db)):
    """Debug endpoint to check supplier-product relationships"""
    supplier_products = db.query(SupplierProduct).all()
    products = db.query(Product).all()
    suppliers = db.query(Supplier).all()
    
    return {
        "total_supplier_products": len(supplier_products),
        "total_products": len(products),
        "total_suppliers": len(suppliers),
        "supplier_products": [
            {
                "id": sp.id,
                "supplier_id": sp.supplier_id,
                "product_id": sp.product_id,
                "cost": float(sp.cost) if sp.cost else None,
                "stock": sp.stock,
                "lead_time_days": sp.lead_time_days,
                "is_active": sp.is_active
            } for sp in supplier_products
        ],
        "products_sample": [
            {
                "id": p.id,
                "name": p.name,
                "sku": p.sku,
                "price": float(p.price) if p.price else None
            } for p in products[:5]  # First 5 products
        ],
        "suppliers_sample": [
            {
                "id": s.id,
                "name": s.name
            } for s in suppliers[:5]  # First 5 suppliers
        ]
    }

@router.get("/supplier-product/")
def get_supplier_products(include_archived: bool = False, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(SupplierProduct)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(SupplierProduct.archived_at.is_(None))
        
    supplier_products = query.offset(skip).limit(limit).all()
    
    # Convert to the same format as other endpoints
    data = [
        {
            "id": sp.id,
            "supplier_id": sp.supplier_id,
            "product_id": sp.product_id,
            "supplier_sku": sp.supplier_sku,
            "cost": float(sp.cost) if sp.cost is not None else None,
            "stock": sp.stock,
            "lead_time_days": sp.lead_time_days,
            "is_active": sp.is_active,
            "notes": sp.notes,
            "archived_at": sp.archived_at,
            "created_at": sp.created_at,
            "last_updated": sp.last_updated,
        }
        for sp in supplier_products
    ]
    return data

@router.get("/{product_id}/supplier-products", response_model=List[SupplierProductResponse])
def get_supplier_products_by_product(product_id: int, include_archived: bool = False, db: Session = Depends(get_db)):
    """Get all supplier-product relationships for a specific product"""
    # Verify product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    query = db.query(SupplierProduct).filter(SupplierProduct.product_id == product_id)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(SupplierProduct.archived_at.is_(None))
        
    supplier_products = query.all()
    return supplier_products

@router.get("/supplier-product/{supplier_product_id}", response_model=SupplierProductResponse)
def get_supplier_product(supplier_product_id: int, include_archived: bool = False, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    query = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id)
    
    # Filter out archived records by default
    if not include_archived:
        query = query.filter(SupplierProduct.archived_at.is_(None))
        
    supplier_product = query.first()
    if supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    return supplier_product

@router.put("/supplier-product/{supplier_product_id}", response_model=SupplierProductResponse)
def update_supplier_product(
    supplier_product_id: int,
    supplier_product: SupplierProductUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    for key, value in supplier_product.model_dump(exclude_unset=True).items():
        setattr(db_supplier_product, key, value)
    
    db.commit()
    db.refresh(db_supplier_product)
    return db_supplier_product

# Archive/Unarchive endpoints for Products
@router.patch("/{product_id}/archive")
def archive_product(product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Archive a product (soft delete)"""
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    
    db_product.archived_at = datetime.utcnow()
    db.commit()
    db.refresh(db_product)
    
    return {"success": True, "data": {"id": product_id, "archived_at": db_product.archived_at}, "error": None, "message": "Product archived successfully"}

@router.patch("/{product_id}/unarchive")
def unarchive_product(product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Unarchive a product (restore from soft delete)"""
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    
    db_product.archived_at = None
    db.commit()
    db.refresh(db_product)
    
    return {"success": True, "data": {"id": product_id, "archived_at": None}, "error": None, "message": "Product restored successfully"}

# Archive/Unarchive endpoints for SupplierProducts
@router.patch("/supplier-product/{supplier_product_id}/archive")
def archive_supplier_product(supplier_product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Archive a supplier-product relationship (soft delete)"""
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    db_supplier_product.archived_at = datetime.utcnow()
    db.commit()
    db.refresh(db_supplier_product)
    
    return {"success": True, "data": {"id": supplier_product_id, "archived_at": db_supplier_product.archived_at}, "error": None, "message": "Supplier Product archived successfully"}

@router.patch("/supplier-product/{supplier_product_id}/unarchive")
def unarchive_supplier_product(supplier_product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    """Unarchive a supplier-product relationship (restore from soft delete)"""
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    db_supplier_product.archived_at = None
    db.commit()
    db.refresh(db_supplier_product)
    
    return {"success": True, "data": {"id": supplier_product_id, "archived_at": None}, "error": None, "message": "Supplier Product restored successfully"}
