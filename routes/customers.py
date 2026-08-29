"""
Customer directory + Customer 360 (roadmap P2).
- GET   /customers?q=&source=&has_purchased=&tag=&offset=   search + filters
- POST  /customers             quick-create (POS customer attach)
- GET   /customers/stats       directory-level aggregates
- GET   /customers/{id}        360 view: profile + WA threads + quotes + AI quotations + docs
- PATCH /customers/{id}        partial profile edit (incl. tags)
"""

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import cast, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from scripts.backfill_customers import normalize_phone

from auth import verify_google_token
from models import (
    get_db,
    Customer,
    WAConversation,
    WAMessage,
    Quote,
    Quotation,
    FileMetadata,
)

router = APIRouter(
    prefix="/customers", tags=["customers"], dependencies=[Depends(verify_google_token)]
)

TAG_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def _escape_like(text: str) -> str:
    """Escape LIKE/ILIKE metacharacters (backslash default escape char)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _brief(c: Customer):
    return {
        "id": c.id,
        "display_name": c.display_name,
        "phone_e164": c.phone_e164,
        "location": c.location,
        "source": c.source,
        "has_purchased": c.has_purchased,
        "tags": c.tags or [],
        "last_activity_at": (
            c.last_activity_at.isoformat() if c.last_activity_at else None
        ),
    }


def _clean_tags(raw: list[str]) -> list[str]:
    """Normalize a tag list: strip, lowercase, drop empties, dedupe preserving
    order. 400 on anything that isn't a valid slug (^[a-z0-9-]{1,40}$)."""
    cleaned: list[str] = []
    for t in raw:
        if not isinstance(t, str):
            raise HTTPException(status_code=400, detail="tags must be strings")
        slug = t.strip().lower()
        if not slug:
            continue
        if not TAG_RE.match(slug):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tag {slug!r}: must match ^[a-z0-9-]{{1,40}}$",
            )
        if slug not in cleaned:
            cleaned.append(slug)
    return cleaned


class CustomerPatch(BaseModel):
    # max_lengths mirror the DB columns — otherwise an oversized value passes
    # pydantic and explodes as a 500 at commit time.
    display_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=255)
    rfc: str | None = Field(None, max_length=20)
    location: str | None = Field(None, max_length=300)
    tags: list[str] | None = None


@router.get("")
def list_customers(
    q: str = "",
    limit: int = Query(60, ge=1, le=200),
    source: str | None = None,
    has_purchased: bool | None = None,
    tag: str | None = None,
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Customer)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Customer.display_name.ilike(like),
                Customer.phone_e164.ilike(like),
                Customer.location.ilike(like),
            )
        )
    if source is not None:
        query = query.filter(Customer.source == source)
    if has_purchased is not None:
        query = query.filter(Customer.has_purchased.is_(has_purchased))
    if tag is not None and tag.strip():
        # tags is JSON (not JSONB): cast for @> containment. NULL tags rows
        # don't match (@> on NULL is NULL, filtered out).
        query = query.filter(cast(Customer.tags, JSONB).contains([tag.strip().lower()]))
    rows = (
        query.order_by(
            Customer.last_activity_at.desc().nullslast(),
            Customer.display_name.asc().nullslast(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_brief(c) for c in rows]


class CustomerCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=200)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    location: str | None = Field(None, max_length=300)


@router.post("")
def create_customer(body: CustomerCreate, db: Session = Depends(get_db)):
    """Quick-create (POS customer attach). Phone is normalized to E.164 with
    the same helper the backfill/WhatsApp scripts use, so the unique
    phone_e164 key stays canonical."""
    display_name = body.display_name.strip()
    if len(display_name) < 2:
        raise HTTPException(status_code=422, detail="display_name muy corto")

    phone_e164 = (
        normalize_phone(body.phone) if body.phone and body.phone.strip() else None
    )
    if phone_e164:
        existing = db.query(Customer).filter(Customer.phone_e164 == phone_e164).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail={
                    "detail": "Ya existe un cliente con ese teléfono",
                    "customer_id": existing.id,
                },
            )

    customer = Customer(
        display_name=display_name[:200],
        # Same fuzzy-merge normalization as scripts/backfill_customers.py
        name_normalized=re.sub(r"\s+", " ", display_name).strip().lower()[:200],
        phone_e164=phone_e164,
        email=body.email.strip()[:255] if body.email and body.email.strip() else None,
        location=(
            body.location.strip()[:300]
            if body.location and body.location.strip()
            else None
        ),
        source="manual",
        first_seen_at=datetime.now(timezone.utc),
    )
    db.add(customer)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent create with the same phone slipped past the pre-check
        db.rollback()
        existing = db.query(Customer).filter(Customer.phone_e164 == phone_e164).first()
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "Ya existe un cliente con ese teléfono",
                "customer_id": existing.id if existing else None,
            },
        )
    db.refresh(customer)
    return _brief(customer)


# NOTE: must be declared BEFORE /{customer_id} or FastAPI matches "stats"
# against the int path param.
@router.get("/stats")
def customer_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Customer.id)).scalar() or 0
    purchased = (
        db.query(func.count(Customer.id))
        .filter(Customer.has_purchased.is_(True))
        .scalar()
        or 0
    )
    sembrando_vida = (
        db.query(func.count(Customer.id))
        .filter(cast(Customer.tags, JSONB).contains(["sembrando-vida"]))
        .scalar()
        or 0
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    active_30d = (
        db.query(func.count(Customer.id))
        .filter(Customer.last_activity_at >= cutoff)
        .scalar()
        or 0
    )
    by_source = {
        (src if src is not None else "unknown"): n
        for src, n in db.query(Customer.source, func.count(Customer.id))
        .group_by(Customer.source)
        .all()
    }
    return {
        "total": total,
        "purchased": purchased,
        "sembrando_vida": sembrando_vida,
        "active_30d": active_30d,
        "by_source": by_source,
    }


@router.get("/{customer_id}")
def customer_360(customer_id: int, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Linked WhatsApp conversations (+ message counts in ONE grouped query)
    convs = (
        db.query(WAConversation).filter(WAConversation.customer_id == customer_id).all()
    )
    counts = {}
    if convs:
        conv_ids = [cv.id for cv in convs]
        counts = dict(
            db.query(WAMessage.conversation_id, func.count(WAMessage.id))
            .filter(WAMessage.conversation_id.in_(conv_ids))
            .group_by(WAMessage.conversation_id)
            .all()
        )
    conversations = [
        {
            "id": cv.id,
            "customer_phone": cv.customer_phone,
            "message_count": counts.get(cv.id, 0),
            "last_message_at": (
                cv.last_message_at.isoformat() if cv.last_message_at else None
            ),
        }
        for cv in convs
    ]

    # Linked trackable quotes
    quotes = [
        {
            "id": q.id,
            "quote_number": q.quote_number,
            "status": q.status,
            "total": float(q.total) if q.total is not None else None,
            "created_at": q.created_at.isoformat() if q.created_at else None,
            "sent_at": q.sent_at.isoformat() if q.sent_at else None,
            "accepted_at": q.accepted_at.isoformat() if q.accepted_at else None,
        }
        for q in db.query(Quote)
        .filter(Quote.customer_id == customer_id)
        .order_by(Quote.created_at.desc())
        .all()
    ]

    # AI-generated quotations (Quotation table) that name this customer.
    # Same >=4-char guard as documents — short names match too much noise.
    # LIKE metacharacters in names (%, _) must be escaped or e.g. "Agro_MX"
    # over-matches unrelated rows.
    ai_quotations = []
    if c.display_name and len(c.display_name.strip()) >= 4:
        like = f"%{_escape_like(c.display_name.strip())}%"
        ai_quotations = [
            {
                "id": aq.id,
                "title": aq.title,
                "created_at": aq.created_at.isoformat() if aq.created_at else None,
            }
            for aq in db.query(Quotation)
            .filter(
                Quotation.customer_name.ilike(like),
                Quotation.archived_at.is_(None),
            )
            .order_by(Quotation.created_at.desc())
            .limit(10)
            .all()
        ]

    # RAG documents that name this customer (their COT/quote PDFs, chats, etc.)
    documents = []
    if c.display_name and len(c.display_name.strip()) >= 4:
        like = f"%{_escape_like(c.display_name.strip())}%"
        docs = (
            db.query(FileMetadata)
            .filter(
                or_(
                    FileMetadata.original_filename.ilike(like),
                    FileMetadata.description.ilike(like),
                ),
                FileMetadata.archived_at.is_(None),
            )
            .order_by(FileMetadata.document_date.desc().nullslast())
            .limit(25)
            .all()
        )
        documents = [
            {
                "id": d.id,
                "filename": d.original_filename,
                "category": d.category,
                "content_type": d.content_type,
                "document_date": (
                    d.document_date.isoformat() if d.document_date else None
                ),
            }
            for d in docs
        ]

    return {
        "customer": _brief(c)
        | {
            "email": c.email,
            "rfc": c.rfc,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
        },
        "conversations": conversations,
        "quotes": quotes,
        "ai_quotations": ai_quotations,
        "documents": documents,
    }


@router.patch("/{customer_id}")
def update_customer(
    customer_id: int, body: CustomerPatch, db: Session = Depends(get_db)
):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if field == "tags":
            # JSON columns don't detect in-place mutation: new list AND flag.
            customer.tags = _clean_tags(value or [])
            flag_modified(customer, "tags")
        else:
            setattr(customer, field, value)
            if field == "display_name" and value:
                # Keep the fuzzy-merge key in sync (same normalization as
                # scripts/backfill_customers.py) or name-keyed backfills
                # would duplicate a renamed customer.
                customer.name_normalized = (
                    re.sub(r"\s+", " ", value).strip().lower()[:200]
                )
    db.commit()
    db.refresh(customer)

    return {
        "success": True,
        "data": _brief(customer)
        | {
            "email": customer.email,
            "rfc": customer.rfc,
            "first_seen_at": (
                customer.first_seen_at.isoformat() if customer.first_seen_at else None
            ),
        },
    }
