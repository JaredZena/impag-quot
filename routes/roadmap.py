"""
In-app roadmap / progress tracker routes.
- GET  /roadmap            list all items (ordered by phase, sort_order)
- POST /roadmap            add a new item
- PUT  /roadmap/{id}       update status / notes / fields
- DELETE /roadmap/{id}     remove an item
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import get_db, RoadmapItem

router = APIRouter(prefix="/roadmap", tags=["roadmap"],
                   dependencies=[Depends(verify_google_token)])

VALID_STATUS = {"planned", "in_progress", "done", "deferred"}


def _dict(r: RoadmapItem):
    return {
        "id": r.id, "phase": r.phase, "title": r.title, "description": r.description,
        "need": r.need, "effort": r.effort, "impact": r.impact, "status": r.status,
        "notes": r.notes, "sort_order": r.sort_order,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("")
def list_items(db: Session = Depends(get_db)):
    rows = db.query(RoadmapItem).order_by(RoadmapItem.phase, RoadmapItem.sort_order).all()
    return [_dict(r) for r in rows]


class ItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    phase: int = 1
    need: Optional[str] = None
    effort: Optional[str] = None
    impact: Optional[str] = None
    status: str = "planned"


class ItemUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    phase: Optional[int] = None
    need: Optional[str] = None
    effort: Optional[str] = None
    impact: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


@router.post("")
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    if data.status not in VALID_STATUS:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(VALID_STATUS)}")
    max_order = db.query(RoadmapItem).count()
    item = RoadmapItem(**data.model_dump(), sort_order=max_order)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _dict(item)


@router.put("/{item_id}")
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)):
    item = db.query(RoadmapItem).filter(RoadmapItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    fields = data.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in VALID_STATUS:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(VALID_STATUS)}")
    for k, v in fields.items():
        setattr(item, k, v)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return _dict(item)


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(RoadmapItem).filter(RoadmapItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"ok": True}
