from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import date, datetime
from models import get_db, LogisticsMetadata, FileMetadata
from auth import verify_google_token

router = APIRouter(prefix="/logistics", tags=["logistics"])


class LogisticsMetadataResponse(BaseModel):
    id: int
    file_id: int
    product_name: Optional[str]
    quantity: Optional[int]
    package_size: Optional[str]
    package_type: Optional[str]
    weight_kg: Optional[float]
    dimensions: Optional[str]
    origin: Optional[str]
    destination: Optional[str]
    carrier: Optional[str]
    tracking_number: Optional[str]
    estimated_delivery: Optional[date]
    cost: Optional[float]
    currency: str
    supplier_product_id: Optional[int]
    supplier_id: Optional[int]
    extraction_confidence: str
    created_at: datetime

    class Config:
        from_attributes = True


class LogisticsUpdateRequest(BaseModel):
    product_name: Optional[str] = None
    quantity: Optional[int] = None
    package_size: Optional[str] = None
    package_type: Optional[str] = None
    weight_kg: Optional[float] = None
    dimensions: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    estimated_delivery: Optional[date] = None
    cost: Optional[float] = None
    currency: Optional[str] = None
    supplier_product_id: Optional[int] = None
    supplier_id: Optional[int] = None


@router.get("/", response_model=List[LogisticsMetadataResponse])
async def list_logistics(
    carrier: Optional[str] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    supplier_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """List logistics records with optional filters."""
    query = db.query(LogisticsMetadata)
    if carrier:
        query = query.filter(LogisticsMetadata.carrier.ilike(f"%{carrier}%"))
    if origin:
        query = query.filter(LogisticsMetadata.origin.ilike(f"%{origin}%"))
    if destination:
        query = query.filter(LogisticsMetadata.destination.ilike(f"%{destination}%"))
    if supplier_id is not None:
        query = query.filter(LogisticsMetadata.supplier_id == supplier_id)

    return query.order_by(LogisticsMetadata.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{logistics_id}", response_model=LogisticsMetadataResponse)
async def get_logistics(
    logistics_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    record = db.query(LogisticsMetadata).filter(LogisticsMetadata.id == logistics_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Logistics record not found")
    return record


@router.get("/by-file/{file_id}", response_model=Optional[LogisticsMetadataResponse])
async def get_logistics_by_file(
    file_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Get logistics metadata for a specific file."""
    return db.query(LogisticsMetadata).filter(LogisticsMetadata.file_id == file_id).first()


@router.put("/{logistics_id}", response_model=LogisticsMetadataResponse)
async def update_logistics(
    logistics_id: int,
    updates: LogisticsUpdateRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(verify_google_token),
):
    """Update logistics metadata (correct AI extraction or link to product)."""
    record = db.query(LogisticsMetadata).filter(LogisticsMetadata.id == logistics_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Logistics record not found")

    for field, value in updates.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(record, field, value)

    db.commit()
    db.refresh(record)
    return record
