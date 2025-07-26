from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from models import get_db, ProductCategory
<<<<<<< HEAD
from auth import verify_google_token
=======
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50

router = APIRouter(prefix="/categories", tags=["categories"])

@router.get("/")
<<<<<<< HEAD
def get_categories(db: Session = Depends(get_db), user: dict = Depends(verify_google_token)):
=======
def get_categories(db: Session = Depends(get_db)):
>>>>>>> b449efd056a66ca365366a3cdad3697783518d50
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