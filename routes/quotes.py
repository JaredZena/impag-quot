from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, or_
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from models import get_db, Quote, QuoteItem, Notification, Product, SupplierProduct, get_next_quote_number
from services.price_calculator import get_product_display_price
from auth import verify_google_token

router = APIRouter(prefix="/quotes", tags=["quotes"])

IVA_RATE = Decimal("0.16")

# ==================== Pydantic Schemas ====================

class QuoteItemCreate(BaseModel):
    product_id: Optional[int] = None
    supplier_product_id: Optional[int] = None
    description: str
    sku: Optional[str] = None
    quantity: float
    unit: Optional[str] = None
    unit_price: float
    iva_applicable: bool = True
    notes: Optional[str] = None
    sort_order: int = 0

class QuoteItemUpdate(BaseModel):
    description: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    iva_applicable: Optional[bool] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None

class QuoteCreate(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_location: Optional[str] = None
    notes: Optional[str] = None
    validity_days: int = 15
    assigned_to: Optional[str] = None
    items: Optional[List[QuoteItemCreate]] = None

class QuoteUpdate(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    customer_location: Optional[str] = None
    notes: Optional[str] = None
    validity_days: Optional[int] = None
    assigned_to: Optional[str] = None
    status: Optional[str] = None

class QuoteItemResponse(BaseModel):
    id: int
    quote_id: int
    product_id: Optional[int] = None
    supplier_product_id: Optional[int] = None
    description: str
    sku: Optional[str] = None
    quantity: float
    unit: Optional[str] = None
    unit_price: float
    iva_applicable: bool
    discount_percent: Optional[float] = None
    discount_amount: Optional[float] = None
    notes: Optional[str] = None
    sort_order: int
    line_total: float = 0

    class Config:
        from_attributes = True

class QuoteResponse(BaseModel):
    id: int
    quote_number: str
    status: str
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_location: Optional[str] = None
    notes: Optional[str] = None
    validity_days: int
    subtotal: float
    iva_amount: float
    total: float
    sent_at: Optional[datetime] = None
    viewed_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None
    created_by: str
    assigned_to: Optional[str] = None
    access_token: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[QuoteItemResponse] = []

    class Config:
        from_attributes = True


# ==================== Helper Functions ====================

def recalculate_totals(quote: Quote, db: Session):
    """Recalculate subtotal, IVA, and total from line items."""
    subtotal = Decimal("0")
    iva_total = Decimal("0")

    for item in quote.items:
        line_total = Decimal(str(item.quantity)) * Decimal(str(item.unit_price))
        subtotal += line_total
        if item.iva_applicable:
            iva_total += line_total * IVA_RATE

    quote.subtotal = subtotal
    quote.iva_amount = iva_total
    quote.total = subtotal + iva_total
    quote.updated_at = datetime.now(timezone.utc)
    db.commit()


def normalize_phone(phone: str) -> str:
    """Ensure phone has +52 prefix for Mexico."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        if phone.startswith("52"):
            phone = "+" + phone
        else:
            phone = "+52" + phone
    return phone


def serialize_quote(quote: Quote) -> dict:
    """Serialize a quote with items for API response."""
    items = []
    for item in quote.items:
        line_total = float(item.quantity) * float(item.unit_price)
        items.append({
            "id": item.id,
            "quote_id": item.quote_id,
            "product_id": item.product_id,
            "supplier_product_id": item.supplier_product_id,
            "description": item.description,
            "sku": item.sku,
            "quantity": float(item.quantity),
            "unit": item.unit,
            "unit_price": float(item.unit_price),
            "iva_applicable": item.iva_applicable,
            "discount_percent": float(item.discount_percent) if item.discount_percent else None,
            "discount_amount": float(item.discount_amount) if item.discount_amount else None,
            "notes": item.notes,
            "sort_order": item.sort_order,
            "line_total": line_total,
        })

    return {
        "id": quote.id,
        "quote_number": quote.quote_number,
        "status": quote.status,
        "customer_name": quote.customer_name,
        "customer_phone": quote.customer_phone,
        "customer_email": quote.customer_email,
        "customer_location": quote.customer_location,
        "notes": quote.notes,
        "validity_days": quote.validity_days,
        "subtotal": float(quote.subtotal),
        "iva_amount": float(quote.iva_amount),
        "total": float(quote.total),
        "sent_at": quote.sent_at.isoformat() if quote.sent_at else None,
        "viewed_at": quote.viewed_at.isoformat() if quote.viewed_at else None,
        "accepted_at": quote.accepted_at.isoformat() if quote.accepted_at else None,
        "expired_at": quote.expired_at.isoformat() if quote.expired_at else None,
        "created_by": quote.created_by,
        "assigned_to": quote.assigned_to,
        "access_token": quote.access_token,
        "created_at": quote.created_at.isoformat() if quote.created_at else None,
        "updated_at": quote.updated_at.isoformat() if quote.updated_at else None,
        "items": items,
    }


# ==================== Quote CRUD ====================

@router.post("")
def create_quote(data: QuoteCreate, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Create a new quote in draft status."""
    quote = Quote(
        quote_number=get_next_quote_number(db),
        status="draft",
        customer_name=data.customer_name,
        customer_phone=normalize_phone(data.customer_phone),
        customer_email=data.customer_email,
        customer_location=data.customer_location,
        notes=data.notes,
        validity_days=data.validity_days,
        created_by=user.get("email", "unknown"),
        assigned_to=data.assigned_to or user.get("email", "unknown"),
    )
    db.add(quote)
    db.flush()  # Get the ID

    # Add items if provided
    if data.items:
        for item_data in data.items:
            item = QuoteItem(
                quote_id=quote.id,
                product_id=item_data.product_id,
                supplier_product_id=item_data.supplier_product_id,
                description=item_data.description,
                sku=item_data.sku,
                quantity=item_data.quantity,
                unit=item_data.unit,
                unit_price=item_data.unit_price,
                iva_applicable=item_data.iva_applicable,
                notes=item_data.notes,
                sort_order=item_data.sort_order,
            )
            db.add(item)

    db.commit()
    db.refresh(quote)
    recalculate_totals(quote, db)
    db.refresh(quote)

    return {"success": True, "data": serialize_quote(quote)}


@router.get("")
def list_quotes(
    status: Optional[str] = None,
    engineer: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user=Depends(verify_google_token),
):
    """List quotes with optional filters."""
    query = db.query(Quote).options(joinedload(Quote.items))

    if status:
        query = query.filter(Quote.status == status)
    if engineer:
        query = query.filter(
            or_(Quote.created_by == engineer, Quote.assigned_to == engineer)
        )
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Quote.customer_name.ilike(search_term),
                Quote.quote_number.ilike(search_term),
                Quote.customer_phone.ilike(search_term),
            )
        )

    total = query.count()
    quotes = query.order_by(desc(Quote.created_at)).offset(offset).limit(limit).all()

    return {
        "success": True,
        "data": [serialize_quote(q) for q in quotes],
        "total": total,
    }


@router.get("/stats")
def quote_stats(db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Quick stats for the quote dashboard."""
    from sqlalchemy import func as sqlfunc
    import datetime

    now = datetime.datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_this_month = db.query(Quote).filter(Quote.created_at >= month_start).count()
    accepted_value = db.query(sqlfunc.sum(Quote.total)).filter(
        Quote.status == "accepted",
        Quote.accepted_at >= month_start,
    ).scalar() or 0
    sent_count = db.query(Quote).filter(Quote.status == "sent").count()
    viewed_count = db.query(Quote).filter(Quote.status == "viewed").count()

    return {
        "success": True,
        "data": {
            "total_this_month": total_this_month,
            "accepted_value": float(accepted_value),
            "pending_sent": sent_count,
            "pending_viewed": viewed_count,
        },
    }


@router.get("/{quote_id}")
def get_quote(quote_id: int, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Get a single quote with items."""
    quote = db.query(Quote).options(joinedload(Quote.items)).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    return {"success": True, "data": serialize_quote(quote)}


@router.put("/{quote_id}")
def update_quote(quote_id: int, data: QuoteUpdate, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Update quote metadata (not items)."""
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "customer_phone" and value:
            value = normalize_phone(value)
        setattr(quote, field, value)

    quote.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(quote)
    return {"success": True, "data": serialize_quote(quote)}


@router.delete("/{quote_id}")
def delete_quote(quote_id: int, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Delete a draft quote."""
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    if quote.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft quotes can be deleted")

    db.delete(quote)
    db.commit()
    return {"success": True, "message": "Quote deleted"}


@router.post("/{quote_id}/send")
def send_quote(quote_id: int, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Mark quote as sent and generate access token."""
    quote = db.query(Quote).options(joinedload(Quote.items)).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    if not quote.items:
        raise HTTPException(status_code=400, detail="Cannot send a quote with no items")
    if not quote.customer_phone:
        raise HTTPException(status_code=400, detail="Customer phone is required to send a quote")

    quote.status = "sent"
    quote.sent_at = datetime.now(timezone.utc)
    quote.access_token = str(uuid.uuid4())
    quote.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(quote)

    return {
        "success": True,
        "data": serialize_quote(quote),
        "quote_url": f"https://todoparaelcampo.com.mx/cotizacion/{quote.access_token}",
    }


# ==================== Quote Items ====================

@router.post("/{quote_id}/items")
def add_item(quote_id: int, data: QuoteItemCreate, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Add a line item to a quote."""
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    item = QuoteItem(
        quote_id=quote_id,
        product_id=data.product_id,
        supplier_product_id=data.supplier_product_id,
        description=data.description,
        sku=data.sku,
        quantity=data.quantity,
        unit=data.unit,
        unit_price=data.unit_price,
        iva_applicable=data.iva_applicable,
        notes=data.notes,
        sort_order=data.sort_order,
    )
    db.add(item)
    db.commit()

    # Recalculate totals
    db.refresh(quote)
    recalculate_totals(quote, db)

    return {"success": True, "data": {"id": item.id, "quote_id": quote_id}}


@router.put("/{quote_id}/items/{item_id}")
def update_item(quote_id: int, item_id: int, data: QuoteItemUpdate, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Update a line item."""
    item = db.query(QuoteItem).filter(QuoteItem.id == item_id, QuoteItem.quote_id == quote_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    db.commit()

    # Recalculate totals
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    recalculate_totals(quote, db)

    return {"success": True, "data": {"id": item.id}}


@router.delete("/{quote_id}/items/{item_id}")
def delete_item(quote_id: int, item_id: int, db: Session = Depends(get_db), user=Depends(verify_google_token)):
    """Delete a line item."""
    item = db.query(QuoteItem).filter(QuoteItem.id == item_id, QuoteItem.quote_id == quote_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    db.delete(item)
    db.commit()

    # Recalculate totals
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    recalculate_totals(quote, db)

    return {"success": True, "message": "Item deleted"}


# ==================== Product Search (for quote form) ====================

@router.get("/product-search/query")
def search_products(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db),
    user=Depends(verify_google_token),
):
    """Search products for the quote form autocomplete."""
    search_term = f"%{q}%"
    results = (
        db.query(SupplierProduct)
        .join(Product, SupplierProduct.product_id == Product.id, isouter=True)
        .filter(
            or_(
                SupplierProduct.name.ilike(search_term),
                SupplierProduct.sku.ilike(search_term),
                Product.name.ilike(search_term),
                Product.sku.ilike(search_term),
            )
        )
        .limit(limit)
        .all()
    )

    products = []
    for sp in results:
        # Calculate display price using standardized formula
        cost = float(sp.cost or 0)
        shipping = float(sp.shipping_cost_direct or 0)
        margin = float(sp.default_margin or 0.25)
        cost_basis = cost + shipping
        display_price = cost_basis / (1 - margin) if margin < 1 else cost_basis

        products.append({
            "supplier_product_id": sp.id,
            "product_id": sp.product_id,
            "name": sp.name or (sp.product.name if sp.product_id else "Unknown"),
            "sku": sp.sku or (sp.product.sku if sp.product_id else None),
            "unit": sp.unit or (sp.product.unit.value if sp.product_id and sp.product and sp.product.unit else "PIEZA"),
            "display_price": round(display_price, 2),
            "iva": sp.iva if sp.iva is not None else True,
        })

    return {"success": True, "data": products}
