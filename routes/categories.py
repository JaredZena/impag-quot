from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from models import get_db, ProductCategory
from auth import verify_google_token

router = APIRouter(prefix="/categories", tags=["categories"])

# GET /categories - PUBLIC for quotation web app
@router.get("/")
@router.get("")  # Handle both /categories and /categories/ explicitly
def get_categories(db: Session = Depends(get_db)):
    categories = db.query(ProductCategory).all()
    data = [
        {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "created_at": c.created_at,
            "last_updated": c.last_updated,
        }
        for c in categories
    ]
    return {"success": True, "data": data, "error": None, "message": None} 