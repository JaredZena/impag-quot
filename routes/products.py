from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, case
from typing import List, Optional, Any
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Product, Supplier, SupplierProduct, ProductUnit
from services.price_calculator import enrich_products_with_calculated_prices, get_product_display_price
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
    default_margin: Optional[float] = None
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
    default_margin: Optional[float] = None
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
    shipping_cost: Optional[float] = None  # Legacy field (deprecated)
    shipping_cost_direct: Optional[float] = 0.00
    shipping_method: Optional[str] = 'DIRECT'
    shipping_stage1_cost: Optional[float] = 0.00
    shipping_stage2_cost: Optional[float] = 0.00
    shipping_stage3_cost: Optional[float] = 0.00
    shipping_stage4_cost: Optional[float] = 0.00
    shipping_notes: Optional[str] = None
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
    shipping_cost: Optional[float] = None  # Legacy field (deprecated)
    shipping_cost_direct: Optional[float] = None
    shipping_method: Optional[str] = None
    shipping_stage1_cost: Optional[float] = None
    shipping_stage2_cost: Optional[float] = None
    shipping_stage3_cost: Optional[float] = None
    shipping_stage4_cost: Optional[float] = None
    shipping_notes: Optional[str] = None
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

# GET /supplier-products - List all supplier-product relationships
@router.get("/supplier-products")
def get_all_supplier_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, le=1000),
    supplier_id: Optional[int] = Query(None),
    product_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Get all supplier-product relationships"""
    try:
        query = db.query(SupplierProduct).options(
            joinedload(SupplierProduct.supplier),
            joinedload(SupplierProduct.product)
        )
        
        # Filter archived
        if not include_archived:
            query = query.filter(SupplierProduct.archived_at.is_(None))
        
        # Apply filters
        if supplier_id:
            query = query.filter(SupplierProduct.supplier_id == supplier_id)
        if product_id:
            query = query.filter(SupplierProduct.product_id == product_id)
        if is_active is not None:
            query = query.filter(SupplierProduct.is_active == is_active)
        
        # Apply pagination
        supplier_products = query.offset(skip).limit(limit).all()
        
        # Build response
        result = []
        for sp in supplier_products:
            try:
                # Calculate total shipping cost
                if hasattr(sp, 'shipping_method') and sp.shipping_method == 'DIRECT':
                    total_shipping = float(getattr(sp, 'shipping_cost_direct', 0) or 0)
                else:
                    total_shipping = float(
                        (getattr(sp, 'shipping_stage1_cost', 0) or 0) +
                        (getattr(sp, 'shipping_stage2_cost', 0) or 0) +
                        (getattr(sp, 'shipping_stage3_cost', 0) or 0) +
                        (getattr(sp, 'shipping_stage4_cost', 0) or 0)
                    )
                
                item = {
                    "id": sp.id,
                    "supplier_id": sp.supplier_id,
                    "supplier_name": sp.supplier.name if sp.supplier else "Unknown",
                    "product_id": sp.product_id,
                    "product_name": sp.product.name if sp.product else "Unknown",
                    "product_sku": sp.product.sku if sp.product else "Unknown",
                    "supplier_sku": sp.supplier_sku or "",
                    "cost": float(sp.cost) if sp.cost else None,
                    "shipping_cost": total_shipping,
                    "total_cost": float(sp.cost or 0) + total_shipping,
                    "shipping_method": getattr(sp, 'shipping_method', 'OCURRE'),
                    "stock": sp.stock or 0,
                    "lead_time_days": sp.lead_time_days or 0,
                    "is_active": sp.is_active,
                    "created_at": sp.created_at,
                    "last_updated": sp.last_updated
                }
                result.append(item)
            except Exception as e:
                # Skip problematic items but log the issue
                print(f"Error processing supplier product {sp.id}: {str(e)}")
                continue
        
        return {
            "success": True,
            "data": {
                "supplier_products": result,
                "total": len(result)
            }
        }
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Error fetching supplier products: {str(e)}")

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
    min_stock: Optional[int] = Query(None, description="Minimum stock level"),
    max_stock: Optional[int] = Query(None, description="Maximum stock level"),
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
        # Normalize spaces for better fuzzy matching
        normalized_search = func.regexp_replace(func.unaccent(name), r'\s+', '', 'g')  # Remove all spaces
        normalized_product = func.regexp_replace(func.unaccent(Product.name), r'\s+', '', 'g')
        
        # First try exact match with unaccent and space handling
        exact_match = func.unaccent(Product.name).ilike(func.unaccent(f"%{name}%"))
        
        # Fuzzy matching with space normalization and lower threshold
        fuzzy_match = func.similarity(normalized_product, normalized_search) > 0.2
        
        # Also try word similarity (handles "malla sombra" vs "mallasombra")
        word_match = func.word_similarity(normalized_search, normalized_product) > 0.2
        
        query = query.filter(exact_match | fuzzy_match | word_match)
        
        # Order by best similarity score
        similarity_score = func.similarity(normalized_product, normalized_search)
        word_similarity_score = func.word_similarity(normalized_search, normalized_product)
        best_score = func.greatest(similarity_score, word_similarity_score)
        
        exact_score = case(
            (exact_match, 1.0),
            else_=best_score
        )
        query = query.order_by(exact_score.desc())
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
    if min_stock is not None:
        query = query.filter(Product.stock >= min_stock)
    if max_stock is not None:
        query = query.filter(Product.stock <= max_stock)
    
    # Add sorting - default sort by name if no sort_by provided
    if not sort_by:
        sort_by = "name"
        
    if sort_by == "name":
        query = query.order_by(Product.name.asc() if sort_order == "asc" else Product.name.desc())
    elif sort_by == "created_at":
        query = query.order_by(Product.created_at.asc() if sort_order == "asc" else Product.created_at.desc())
    elif sort_by == "last_updated":
        query = query.order_by(Product.last_updated.asc() if sort_order == "asc" else Product.last_updated.desc())
    elif sort_by == "category_name":
        # Join with ProductCategory to sort by category name
        from models import ProductCategory
        query = query.join(ProductCategory, Product.category_id == ProductCategory.id, isouter=True)
        query = query.order_by(ProductCategory.name.asc() if sort_order == "asc" else ProductCategory.name.desc())
    else:
        # Default fallback to name sorting
        query = query.order_by(Product.name.asc())
    
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
            "price": float(p.price) if p.price is not None else (float(p.calculated_price) if p.calculated_price is not None else None),
            "stock": p.stock,
            "specifications": p.specifications,
            "default_margin": float(p.default_margin) if p.default_margin is not None else None,
            "calculated_price": float(p.calculated_price) if p.calculated_price is not None else None,
            "is_calculated_price": p.price is None and p.calculated_price is not None,
            "embedded": p.embedded,
            "is_active": p.is_active,
            "archived_at": p.archived_at,
            "created_at": p.created_at,
            "last_updated": p.last_updated,
        }
        for p in products
    ]
    
    return {"success": True, "data": data, "error": None, "message": None}

# GET /products/stock - Get products in stock (must be before /{product_id})
@router.get("/stock")
def get_products_in_stock(
    db: Session = Depends(get_db),
    min_stock: int = Query(default=1, description="Minimum stock level to include"),
    include_zero_stock: bool = Query(default=False, description="Include products with zero stock"),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    sort_by: Optional[str] = Query(default="name", description="Column to sort by: stock, price, total_value, last_updated, name"),
    sort_order: Optional[str] = Query(default="asc", description="Sort order: asc or desc")
):
    """Get products that are currently in stock"""
    query = db.query(Product).filter(Product.archived_at.is_(None))
    
    if include_zero_stock:
        query = query.filter(Product.stock >= 0)
    else:
        query = query.filter(Product.stock >= min_stock)
    
    # Add sorting
    if sort_by == "stock":
        order_field = Product.stock
    elif sort_by == "price":
        order_field = Product.price
    elif sort_by == "last_updated":
        order_field = Product.last_updated
    elif sort_by == "name":
        order_field = Product.name
    elif sort_by == "total_value":
        # For total_value, we need to calculate it in the query
        order_field = Product.stock * Product.price
    else:
        order_field = Product.name  # Default fallback
    
    # Apply sort direction
    if sort_order.lower() == "desc":
        query = query.order_by(order_field.desc())
    else:
        query = query.order_by(order_field.asc())
    
    total = query.count()
    products = query.offset(offset).limit(limit).all()
    
    stock_data = []
    for product in products:
        total_value = None
        if product.stock and product.price:
            total_value = float(product.stock * product.price)
        
        stock_data.append({
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "unit": product.unit.value if product.unit else ProductUnit.PIEZA.value,
            "stock": product.stock,
            "price": float(product.price) if product.price else None,
            "total_value": total_value,
            "last_updated": product.last_updated
        })
    
    return {
        "success": True,
        "data": {
            "products": stock_data,
            "total": total,
            "offset": offset,
            "limit": limit
        },
        "error": None,
        "message": None
    }

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
        "price": float(product.price) if product.price is not None else (float(product.calculated_price) if product.calculated_price is not None else None),
        "stock": product.stock,
        "specifications": product.specifications,
        "default_margin": float(product.default_margin) if product.default_margin is not None else None,
        "calculated_price": float(product.calculated_price) if product.calculated_price is not None else None,
        "is_calculated_price": product.price is None and product.calculated_price is not None,
        "embedded": product.embedded,
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
        "default_margin": float(db_product.default_margin) if db_product.default_margin is not None else None,
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
        "default_margin": float(db_product.default_margin) if db_product.default_margin is not None else None,
        "is_active": db_product.is_active,
        "archived_at": db_product.archived_at,
        "created_at": db_product.created_at,
        "last_updated": db_product.last_updated,
    }
    return {"success": True, "data": data, "error": None, "message": None}

# SupplierProduct endpoints - ALL REQUIRE AUTHENTICATION for admin operations
@router.post("/supplier-product/", response_model=SupplierProductResponse)
@router.post("/supplier-products", response_model=SupplierProductResponse)  # Add plural endpoint for frontend compatibility
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

# Update shipping info for supplier product (from balance page)
@router.patch("/supplier-product/{supplier_product_id}/shipping", response_model=SupplierProductResponse)
def update_supplier_product_shipping(
    supplier_product_id: int,
    shipping_data: dict,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """Update shipping method and costs for a supplier product"""
    db_supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == supplier_product_id).first()
    if db_supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product not found")
    
    # Update shipping method
    if 'shipping_method' in shipping_data:
        db_supplier_product.shipping_method = shipping_data['shipping_method']
    
    # Update shipping costs based on method
    if shipping_data.get('shipping_method') == 'DIRECT':
        if 'shipping_cost_direct' in shipping_data:
            db_supplier_product.shipping_cost_direct = shipping_data['shipping_cost_direct']
        # Reset stage costs when switching to DIRECT
        db_supplier_product.shipping_stage1_cost = 0.0
        db_supplier_product.shipping_stage2_cost = 0.0
        db_supplier_product.shipping_stage3_cost = 0.0
        db_supplier_product.shipping_stage4_cost = 0.0
    elif shipping_data.get('shipping_method') == 'OCURRE':
        # Update stage costs
        if 'shipping_stage1_cost' in shipping_data:
            db_supplier_product.shipping_stage1_cost = shipping_data['shipping_stage1_cost']
        if 'shipping_stage2_cost' in shipping_data:
            db_supplier_product.shipping_stage2_cost = shipping_data['shipping_stage2_cost']
        if 'shipping_stage3_cost' in shipping_data:
            db_supplier_product.shipping_stage3_cost = shipping_data['shipping_stage3_cost']
        if 'shipping_stage4_cost' in shipping_data:
            db_supplier_product.shipping_stage4_cost = shipping_data['shipping_stage4_cost']
        # Reset direct cost when switching to OCURRE
        db_supplier_product.shipping_cost_direct = 0.0
    
    # Update shipping notes if provided
    if 'shipping_notes' in shipping_data:
        db_supplier_product.shipping_notes = shipping_data['shipping_notes']
    
    db.commit()
    db.refresh(db_supplier_product)
    return db_supplier_product

# Get supplier product by supplier_id and product_id
@router.get("/supplier-product/by-relationship/{supplier_id}/{product_id}", response_model=SupplierProductResponse)
def get_supplier_product_by_relationship(
    supplier_id: int,
    product_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """Get supplier product by supplier_id and product_id"""
    supplier_product = db.query(SupplierProduct).filter(
        SupplierProduct.supplier_id == supplier_id,
        SupplierProduct.product_id == product_id,
        SupplierProduct.archived_at.is_(None)
    ).first()
    
    if supplier_product is None:
        raise HTTPException(status_code=404, detail="Supplier Product relationship not found")
    
    return supplier_product

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

# Stock Management Endpoints

class StockUpdateItem(BaseModel):
    product_id: int
    stock: int
    price: Optional[float] = None

class BulkStockUpdate(BaseModel):
    updates: List[StockUpdateItem]

class StockResponse(BaseModel):
    id: int
    name: str
    sku: str
    unit: str
    stock: int
    price: Optional[float]
    total_value: Optional[float]
    last_updated: Optional[datetime]


@router.patch("/{product_id}/stock")
def update_product_stock(
    product_id: int,
    stock: int,
    price: Optional[float] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """Update stock level for a single product"""
    db_product = db.query(Product).filter(Product.id == product_id).first()
    if db_product is None:
        return {"success": False, "data": None, "error": "Product not found", "message": None}
    
    db_product.stock = stock
    if price is not None:
        db_product.price = price
    db_product.last_updated = datetime.utcnow()
    
    db.commit()
    db.refresh(db_product)
    
    total_value = None
    if db_product.stock and db_product.price:
        total_value = float(db_product.stock * db_product.price)
    
    return {
        "success": True,
        "data": {
            "id": db_product.id,
            "name": db_product.name,
            "sku": db_product.sku,
            "stock": db_product.stock,
            "price": float(db_product.price) if db_product.price else None,
            "total_value": total_value,
            "last_updated": db_product.last_updated
        },
        "error": None,
        "message": "Stock updated successfully"
    }

@router.post("/stock/bulk-update")
def bulk_update_stock(
    bulk_update: BulkStockUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """Update stock levels for multiple products"""
    updated_products = []
    errors = []
    
    for update_item in bulk_update.updates:
        try:
            db_product = db.query(Product).filter(Product.id == update_item.product_id).first()
            if db_product is None:
                errors.append(f"Product with ID {update_item.product_id} not found")
                continue
            
            db_product.stock = update_item.stock
            if update_item.price is not None:
                db_product.price = update_item.price
            db_product.last_updated = datetime.utcnow()
            
            updated_products.append({
                "id": db_product.id,
                "name": db_product.name,
                "sku": db_product.sku,
                "stock": db_product.stock,
                "price": float(db_product.price) if db_product.price else None
            })
        except Exception as e:
            errors.append(f"Error updating product {update_item.product_id}: {str(e)}")
    
    db.commit()
    
    return {
        "success": len(errors) == 0,
        "data": {
            "updated_products": updated_products,
            "updated_count": len(updated_products),
            "errors": errors
        },
        "error": None if len(errors) == 0 else "Some updates failed",
        "message": f"Updated {len(updated_products)} products successfully"
    }
