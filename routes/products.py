from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Product, Supplier, SupplierProduct, ProductUnit

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
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@router.put("/{product_id}", response_model=Product)
def update_product(product_id: int, product: ProductCreate, db: Session = Depends(get_db)):
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    
    for key, value in product.model_dump().items():
        setattr(db_product, key, value)
    
    db.commit()
    db.refresh(db_product)
    return db_product

# SupplierProduct endpoints
@router.post("/supplier-product/", response_model=SupplierProduct)
def create_supplier_product(supplier_product: SupplierProductCreate, db: Session = Depends(get_db)):
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

@router.get("/supplier-product/", response_model=List[SupplierProduct])
def get_supplier_products(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    supplier_products = db.query(SupplierProduct).offset(skip).limit(limit).all()
    return supplier_products

@router.get("/supplier-product/{supplier_product_id}", response_model=SupplierProduct)
def get_supplier_product(supplier_product_id: int, db: Session = Depends(get_db)):
    supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    return supplier_product

@router.put("/supplier-product/{supplier_product_id}", response_model=SupplierProduct)
def update_supplier_product(
    supplier_product_id: int,
    supplier_product: SupplierProductCreate,
    db: Session = Depends(get_db)
):
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    for key, value in supplier_product.model_dump().items():
        setattr(db_supplier_product, key, value)
    
    db.commit()
    db.refresh(db_supplier_product)
    return db_supplier_product 