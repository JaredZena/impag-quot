from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
<<<<<<< HEAD
from models import get_db, Product, Supplier, SupplierProduct, ProductUnit
=======
from models import get_db, Product, Supplier, SupplierProduct, ProductUnit, ProductVariant
from auth import verify_google_token
>>>>>>> 6bc8303 (adding oauth authentication)

router = APIRouter(prefix="/products", tags=["products"])

# Pydantic models for request/response
class ProductBase(BaseModel):
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    specifications: Optional[str] = None
    unit: Optional[ProductUnit] = ProductUnit.PIEZA
    package_size: Optional[int] = None
    iva: Optional[bool] = True

class ProductCreate(ProductBase):
    pass

class Product(ProductBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class SupplierProductBase(BaseModel):
    supplier_id: int
    product_id: int
    base_price: Optional[float] = None
    min_margin: Optional[float] = None
    max_margin: Optional[float] = None
    stock: Optional[int] = 0
    notes: Optional[str] = None

class SupplierProductCreate(SupplierProductBase):
    pass

class SupplierProduct(SupplierProductBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

<<<<<<< HEAD
# Product endpoints
@router.post("/", response_model=Product)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    db_product = Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@router.get("/", response_model=List[Product])
def get_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    products = db.query(Product).offset(skip).limit(limit).all()
    return products

@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int, db: Session = Depends(get_db)):
=======
# GET /products with advanced filtering and JSON wrapper
@router.get("/")
def get_products(
    id: Optional[int] = Query(None),
    name: Optional[str] = Query(None),
    sku: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    query = db.query(Product)
    if id:
        query = query.filter(Product.id == id)
    if name:
        query = query.filter(Product.name.ilike(f"%{name}%"))
    if sku:
        like_pattern = f"%{sku}%"
        # Join with variants and filter by either base_sku or any variant's sku
        query = query.outerjoin(Product.variants).filter(
            (Product.base_sku.ilike(like_pattern)) |
            (ProductVariant.sku.ilike(like_pattern))
        )
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if is_active is not None:
        query = query.join(Product.variants).filter(ProductVariant.is_active == is_active)
    if supplier_id:
        query = query.join(Product.variants).join(ProductVariant.supplier_products).filter(SupplierProduct.supplier_id == supplier_id)
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
            "created_at": p.created_at,
            "last_updated": p.last_updated,
        }
        for p in products
    ]
    return {"success": True, "data": data, "error": None, "message": None}

# GET /products/{product_id}
@router.get("/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

<<<<<<< HEAD
@router.put("/{product_id}", response_model=Product)
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db)):
=======
# PUT /products/{product_id}
@router.put("/{product_id}")
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    
    for key, value in product.model_dump().items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    return db_product

# SupplierProduct endpoints
<<<<<<< HEAD
@router.post("/supplier-product/", response_model=SupplierProduct)
def create_supplier_product(supplier_product: SupplierProductCreate, db: Session = Depends(get_db)):
=======
@router.post("/supplier-product/", response_model=SupplierProductResponse)
def create_supplier_product(supplier_product: SupplierProductCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
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

<<<<<<< HEAD
@router.get("/supplier-product/", response_model=List[SupplierProduct])
def get_supplier_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    supplier_products = db.query(SupplierProduct).offset(skip).limit(limit).all()
    return supplier_products

@router.get("/supplier-product/{supplier_product_id}", response_model=SupplierProduct)
def get_supplier_product(supplier_product_id: int, db: Session = Depends(get_db)):
=======
@router.get("/supplier-product/", response_model=List[SupplierProductResponse])
def get_supplier_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
    supplier_products = db.query(SupplierProduct).offset(skip).limit(limit).all()
    return supplier_products

@router.get("/supplier-product/{supplier_product_id}", response_model=SupplierProductResponse)
def get_supplier_product(supplier_product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
>>>>>>> 6bc8303 (adding oauth authentication)
    supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    return supplier_product

@router.put("/supplier-product/{supplier_product_id}", response_model=SupplierProduct)
def update_supplier_product(
    supplier_product_id: int,
    supplier_product: SupplierProductCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    for key, value in supplier_product.model_dump().items():
        setattr(db_supplier_product, key, value)
    
    db.commit()
    db.refresh(db_supplier_product)
<<<<<<< HEAD
    return db_supplier_product 
=======
    return db_supplier_product 

# Variant models
class VariantBase(BaseModel):
    sku: str
    price: Optional[float] = None
    stock: Optional[int] = 0
    specifications: Optional[Any] = None
    is_active: Optional[bool] = True

class VariantCreate(VariantBase):
    pass

# Variant endpoints
# GET /products/{product_id}/variants
@router.get("/{product_id}/variants")
def get_variants_for_product(product_id: int, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
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

# POST /products/{product_id}/variants
@router.post("/{product_id}/variants")
def create_variant(product_id: int, variant: VariantCreate, db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
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
>>>>>>> 6bc8303 (adding oauth authentication)
