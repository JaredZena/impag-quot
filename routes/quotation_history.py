from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from models import get_db, Quotation
from auth import verify_google_token

router = APIRouter(prefix="/quotation-history", tags=["quotation-history"])

class QuotationCreate(BaseModel):
    user_query: str
    title: Optional[str] = None
    customer_name: Optional[str] = None
    customer_location: Optional[str] = None
    quotation_id: Optional[str] = None
    internal_quotation: str
    customer_quotation: str
    raw_response: Optional[str] = None

class QuotationResponse(BaseModel):
    id: int
    user_id: str
    user_email: str
    user_query: str
    title: Optional[str]
    customer_name: Optional[str]
    customer_location: Optional[str]
    quotation_id: Optional[str]
    internal_quotation: str
    customer_quotation: str
    raw_response: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

@router.post("/", response_model=QuotationResponse)
async def create_quotation(
    quotation: QuotationCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Save a generated quotation (both internal and customer-facing versions).
    """
    # Generate title from query if not provided
    title = quotation.title
    if not title:
        # Use first 50 characters of query as title
        title = quotation.user_query[:50] + ("..." if len(quotation.user_query) > 50 else "")
    
    db_quotation = Quotation(
        user_id=user["user_id"],
        user_email=user["email"],
        user_query=quotation.user_query,
        title=title,
        customer_name=quotation.customer_name,
        customer_location=quotation.customer_location,
        quotation_id=quotation.quotation_id,
        internal_quotation=quotation.internal_quotation,
        customer_quotation=quotation.customer_quotation,
        raw_response=quotation.raw_response
    )
    
    db.add(db_quotation)
    db.commit()
    db.refresh(db_quotation)
    
    return db_quotation

@router.get("/", response_model=List[QuotationResponse])
async def get_quotations(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Get all quotations for the authenticated user, ordered by most recent.
    """
    quotations = db.query(Quotation).filter(
        Quotation.user_id == user["user_id"]
    ).order_by(Quotation.created_at.desc()).offset(skip).limit(limit).all()
    
    return quotations

@router.get("/{quotation_id}", response_model=QuotationResponse)
async def get_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Get a specific quotation by ID (only if it belongs to the authenticated user).
    """
    quotation = db.query(Quotation).filter(
        Quotation.id == quotation_id,
        Quotation.user_id == user["user_id"]
    ).first()
    
    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    
    return quotation

@router.delete("/{quotation_id}")
async def delete_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token)
):
    """
    Delete a quotation (only if it belongs to the authenticated user).
    """
    quotation = db.query(Quotation).filter(
        Quotation.id == quotation_id,
        Quotation.user_id == user["user_id"]
    ).first()
    
    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    
    db.delete(quotation)
    db.commit()
    
    return {"message": "Quotation deleted successfully"}

