"""
Customer directory + Customer 360 (roadmap P2).
- GET /customers?q=            search by name / phone / location
- GET /customers/{id}         360 view: profile + WA threads + quotes + docs
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import get_db, Customer, WAConversation, WAMessage, Quote, FileMetadata

router = APIRouter(prefix="/customers", tags=["customers"],
                   dependencies=[Depends(verify_google_token)])


def _brief(c: Customer):
    return {
        "id": c.id, "display_name": c.display_name, "phone_e164": c.phone_e164,
        "location": c.location, "source": c.source, "has_purchased": c.has_purchased,
        "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
    }


@router.get("")
def list_customers(q: str = "", limit: int = 60, db: Session = Depends(get_db)):
    query = db.query(Customer)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            Customer.display_name.ilike(like),
            Customer.phone_e164.ilike(like),
            Customer.location.ilike(like),
        ))
    rows = (query.order_by(Customer.last_activity_at.desc().nullslast(),
                           Customer.display_name.asc().nullslast())
            .limit(min(limit, 200)).all())
    return [_brief(c) for c in rows]


@router.get("/{customer_id}")
def customer_360(customer_id: int, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Linked WhatsApp conversations (+ message counts)
    convs = db.query(WAConversation).filter(WAConversation.customer_id == customer_id).all()
    conversations = []
    for cv in convs:
        n = db.query(WAMessage).filter(WAMessage.conversation_id == cv.id).count()
        conversations.append({"id": cv.id, "customer_phone": cv.customer_phone,
                              "message_count": n,
                              "last_message_at": cv.last_message_at.isoformat() if cv.last_message_at else None})

    # Linked trackable quotes
    quotes = [{
        "id": q.id, "quote_number": q.quote_number, "status": q.status,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    } for q in db.query(Quote).filter(Quote.customer_id == customer_id)
        .order_by(Quote.created_at.desc()).all()]

    # RAG documents that name this customer (their COT/quote PDFs, chats, etc.)
    documents = []
    if c.display_name and len(c.display_name.strip()) >= 4:
        like = f"%{c.display_name.strip()}%"
        docs = (db.query(FileMetadata)
                .filter(or_(FileMetadata.original_filename.ilike(like),
                            FileMetadata.description.ilike(like)),
                        FileMetadata.archived_at.is_(None))
                .order_by(FileMetadata.document_date.desc().nullslast())
                .limit(25).all())
        documents = [{"id": d.id, "filename": d.original_filename, "category": d.category,
                      "document_date": d.document_date.isoformat() if d.document_date else None}
                     for d in docs]

    return {"customer": _brief(c) | {"email": c.email, "rfc": c.rfc,
                                     "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None},
            "conversations": conversations, "quotes": quotes, "documents": documents}
