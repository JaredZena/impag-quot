from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List

from auth import verify_google_token
from models import get_db, TaskCategory, Task, get_current_task_user

router = APIRouter(prefix="/task-categories", tags=["task-categories"])


class CategoryCreate(BaseModel):
    name: str
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None


class ReorderBody(BaseModel):
    order: List[int]


def serialize_category(cat, task_count=0):
    if not cat:
        return None
    return {
        "id": cat.id,
        "name": cat.name,
        "color": cat.color,
        "icon": cat.icon,
        "sort_order": cat.sort_order,
        "task_count": task_count,
    }


@router.get("")
def list_categories(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task_count_subq = db.query(
        Task.category_id,
        func.count(Task.id).label("task_count")
    ).filter(Task.status != "archived").group_by(Task.category_id).subquery()

    results = db.query(TaskCategory, task_count_subq.c.task_count).outerjoin(
        task_count_subq, TaskCategory.id == task_count_subq.c.category_id
    ).filter(
        TaskCategory.is_active == True
    ).order_by(TaskCategory.sort_order.asc()).all()

    data = [serialize_category(cat, count or 0) for cat, count in results]
    return {"success": True, "data": data, "error": None, "message": None}


@router.post("", status_code=201)
def create_category(
    body: CategoryCreate,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    max_order = db.query(func.max(TaskCategory.sort_order)).filter(
        TaskCategory.is_active == True
    ).scalar() or 0

    category = TaskCategory(
        name=body.name,
        color=body.color,
        icon=body.icon,
        created_by=current_user.id,
        sort_order=max_order + 1,
    )
    db.add(category)
    db.commit()
    db.refresh(category)

    return {"success": True, "data": serialize_category(category), "error": None, "message": "Category created"}


@router.put("/reorder")
def reorder_categories(
    body: ReorderBody,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    for idx, category_id in enumerate(body.order):
        cat = db.query(TaskCategory).filter(TaskCategory.id == category_id).first()
        if cat:
            cat.sort_order = idx
    db.commit()
    return {"success": True, "data": None, "error": None, "message": "Categories reordered"}


@router.put("/{category_id}")
def update_category(
    category_id: int,
    body: CategoryUpdate,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    category = db.query(TaskCategory).filter(
        TaskCategory.id == category_id,
        TaskCategory.is_active == True,
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    for field in ["name", "color", "icon", "sort_order"]:
        val = getattr(body, field)
        if val is not None:
            setattr(category, field, val)

    db.commit()
    db.refresh(category)
    return {"success": True, "data": serialize_category(category), "error": None, "message": "Category updated"}


@router.delete("/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    category = db.query(TaskCategory).filter(
        TaskCategory.id == category_id,
        TaskCategory.is_active == True,
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    category.is_active = False
    db.commit()
    return {"success": True, "data": {"id": category_id}, "error": None, "message": "Category deleted"}
