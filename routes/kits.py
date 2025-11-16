from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Kit, KitItem, Product, SupplierProduct
from auth import verify_google_token

router = APIRouter(prefix="/kits", tags=["kits"])

# Pydantic models
class KitItemCreate(BaseModel):
    supplier_product_id: int  # NEW - primary field
    product_id: Optional[int] = None  # Keep for backward compatibility
    quantity: int = 1
    unit_price: Optional[float] = None
    notes: Optional[str] = None

class KitItemResponse(BaseModel):
    id: int
    supplier_product_id: Optional[int]  # NEW - primary field
    product_id: Optional[int]  # Keep for backward compatibility
    product_name: str
    product_sku: str
    supplier_name: Optional[str] = None  # NEW - include supplier info
    quantity: int
    unit_price: Optional[float]
    notes: Optional[str]
    
    class Config:
        from_attributes = True

class KitCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sku: str
    price: Optional[float] = None
    margin: Optional[float] = None
    items: List[KitItemCreate] = []

class KitUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    margin: Optional[float] = None
    is_active: Optional[bool] = None
    items: Optional[List[KitItemCreate]] = None

class KitResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    sku: str
    price: Optional[float]
    margin: Optional[float]
    is_active: bool
    created_at: datetime
    last_updated: Optional[datetime]
    items: List[KitItemResponse] = []
    calculated_cost: Optional[float] = None  # Sum of all item costs
    
    class Config:
        from_attributes = True

# GET /kits - List all kits
@router.get("/", response_model=List[KitResponse])
@router.get("", response_model=List[KitResponse])  # Handle both /kits and /kits/ explicitly
def get_kits(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, le=1000),
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    include_archived: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Get all kits with optional filtering"""
    query = db.query(Kit).options(
        joinedload(Kit.items).joinedload(KitItem.supplier_product).joinedload(SupplierProduct.supplier),
        joinedload(Kit.items).joinedload(KitItem.product)  # Keep for backward compatibility
    )
    
    # Filter archived
    if not include_archived:
        query = query.filter(Kit.archived_at.is_(None))
    
    # Filter by active status
    if is_active is not None:
        query = query.filter(Kit.is_active == is_active)
    
    # Search filter
    if search:
        query = query.filter(
            Kit.name.ilike(f"%{search}%") |
            Kit.description.ilike(f"%{search}%") |
            Kit.sku.ilike(f"%{search}%")
        )
    
    # Apply pagination
    kits = query.offset(skip).limit(limit).all()
    
    # Calculate costs for each kit
    result = []
    for kit in kits:
        kit_dict = {
            "id": kit.id,
            "name": kit.name,
            "description": kit.description,
            "sku": kit.sku,
            "price": float(kit.price) if kit.price else None,
            "margin": float(kit.margin) if kit.margin else None,
            "is_active": kit.is_active,
            "created_at": kit.created_at,
            "last_updated": kit.last_updated,
            "items": [
                {
                    "id": item.id,
                    "supplier_product_id": item.supplier_product_id,
                    "product_id": item.product_id,  # Keep for backward compatibility
                    "product_name": item.supplier_product.name if item.supplier_product else (item.product.name if item.product else "Unknown"),
                    "product_sku": item.supplier_product.sku if item.supplier_product else (item.product.sku if item.product else "N/A"),
                    "supplier_name": item.supplier_product.supplier.name if item.supplier_product and item.supplier_product.supplier else None,
                    "quantity": item.quantity,
                    "unit_price": float(item.unit_price) if item.unit_price else (float(item.supplier_product.cost) if item.supplier_product and item.supplier_product.cost else (float(item.product.price) if item.product and item.product.price else None)),
                    "notes": item.notes
                }
                for item in kit.items
            ]
        }
        
        # Calculate total cost
        calculated_cost = 0
        for item in kit.items:
            item_price = item.unit_price or (item.supplier_product.cost if item.supplier_product and item.supplier_product.cost else (item.product.price if item.product and item.product.price else 0))
            calculated_cost += float(item_price) * item.quantity
        
        kit_dict["calculated_cost"] = calculated_cost if calculated_cost > 0 else None
        result.append(kit_dict)
    
    return result

# GET /kits/{kit_id} - Get single kit
@router.get("/{kit_id}", response_model=KitResponse)
def get_kit(kit_id: int, db: Session = Depends(get_db)):
    """Get a single kit by ID"""
    kit = db.query(Kit).options(
        joinedload(Kit.items).joinedload(KitItem.supplier_product).joinedload(SupplierProduct.supplier),
        joinedload(Kit.items).joinedload(KitItem.product)  # Keep for backward compatibility
    ).filter(Kit.id == kit_id).first()
    
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")
    
    # Build response with calculated cost
    kit_dict = {
        "id": kit.id,
        "name": kit.name,
        "description": kit.description,
        "sku": kit.sku,
        "price": float(kit.price) if kit.price else None,
        "margin": float(kit.margin) if kit.margin else None,
        "is_active": kit.is_active,
        "created_at": kit.created_at,
        "last_updated": kit.last_updated,
        "items": [
            {
                "id": item.id,
                "supplier_product_id": item.supplier_product_id,
                "product_id": item.product_id,  # Keep for backward compatibility
                "product_name": item.supplier_product.name if item.supplier_product else (item.product.name if item.product else "Unknown"),
                "product_sku": item.supplier_product.sku if item.supplier_product else (item.product.sku if item.product else "N/A"),
                "supplier_name": item.supplier_product.supplier.name if item.supplier_product and item.supplier_product.supplier else None,
                "quantity": item.quantity,
                "unit_price": float(item.unit_price) if item.unit_price else (float(item.supplier_product.cost) if item.supplier_product and item.supplier_product.cost else (float(item.product.price) if item.product and item.product.price else None)),
                "notes": item.notes
            }
            for item in kit.items
        ]
    }
    
    # Calculate total cost
    calculated_cost = 0
    for item in kit.items:
        item_price = item.unit_price or (item.supplier_product.cost if item.supplier_product and item.supplier_product.cost else (item.product.price if item.product and item.product.price else 0))
        calculated_cost += float(item_price) * item.quantity
    
    kit_dict["calculated_cost"] = calculated_cost if calculated_cost > 0 else None
    
    return kit_dict

# POST /kits - Create new kit
@router.post("/", response_model=KitResponse)
def create_kit(kit_data: KitCreate, db: Session = Depends(get_db)):
    """Create a new kit"""
    
    # Check if SKU already exists
    existing_kit = db.query(Kit).filter(Kit.sku == kit_data.sku).first()
    if existing_kit:
        raise HTTPException(status_code=400, detail="SKU already exists")
    
    # Create kit
    db_kit = Kit(
        name=kit_data.name,
        description=kit_data.description,
        sku=kit_data.sku,
        price=kit_data.price,
        margin=kit_data.margin
    )
    
    db.add(db_kit)
    db.flush()  # Get the kit ID
    
    # Add items
    for item_data in kit_data.items:
        # Verify supplier product exists
        supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == item_data.supplier_product_id).first()
        if not supplier_product:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Supplier product {item_data.supplier_product_id} not found")
        
        db_item = KitItem(
            kit_id=db_kit.id,
            supplier_product_id=item_data.supplier_product_id,
            product_id=item_data.product_id,  # Keep for backward compatibility
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            notes=item_data.notes
        )
        db.add(db_item)
    
    db.commit()
    
    # Return the created kit
    return get_kit(db_kit.id, db)

# PUT /kits/{kit_id} - Update kit
@router.put("/{kit_id}", response_model=KitResponse)
def update_kit(kit_id: int, kit_data: KitUpdate, db: Session = Depends(get_db)):
    """Update an existing kit"""
    
    kit = db.query(Kit).filter(Kit.id == kit_id).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")
    
    # Update kit fields
    if kit_data.name is not None:
        kit.name = kit_data.name
    if kit_data.description is not None:
        kit.description = kit_data.description
    if kit_data.price is not None:
        kit.price = kit_data.price
    if kit_data.margin is not None:
        kit.margin = kit_data.margin
    if kit_data.is_active is not None:
        kit.is_active = kit_data.is_active
    
    # Update items if provided
    if kit_data.items is not None:
        # Delete existing items
        db.query(KitItem).filter(KitItem.kit_id == kit_id).delete()
        
        # Add new items
        for item_data in kit_data.items:
            # Verify supplier product exists
            supplier_product = db.query(SupplierProduct).filter(SupplierProduct.id == item_data.supplier_product_id).first()
            if not supplier_product:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Supplier product {item_data.supplier_product_id} not found")
            
            db_item = KitItem(
                kit_id=kit_id,
                supplier_product_id=item_data.supplier_product_id,
                product_id=item_data.product_id,  # Keep for backward compatibility
                quantity=item_data.quantity,
                unit_price=item_data.unit_price,
                notes=item_data.notes
            )
            db.add(db_item)
    
    db.commit()
    
    # Return the updated kit
    return get_kit(kit_id, db)

# DELETE /kits/{kit_id} - Archive kit
@router.delete("/{kit_id}")
def archive_kit(kit_id: int, db: Session = Depends(get_db)):
    """Archive a kit (soft delete)"""
    
    kit = db.query(Kit).filter(Kit.id == kit_id).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")
    
    kit.archived_at = func.now()
    kit.is_active = False
    db.commit()
    
    return {"message": "Kit archived successfully"}

# POST /kits/{kit_id}/restore - Restore archived kit
@router.post("/{kit_id}/restore")
def restore_kit(kit_id: int, db: Session = Depends(get_db)):
    """Restore an archived kit"""
    
    kit = db.query(Kit).filter(Kit.id == kit_id).first()
    if not kit:
        raise HTTPException(status_code=404, detail="Kit not found")
    
    kit.archived_at = None
    kit.is_active = True
    db.commit()
    
    return {"message": "Kit restored successfully"}