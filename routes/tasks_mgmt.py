from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, date

from auth import verify_google_token
from models import get_db, Task, TaskUser, TaskCategory, TaskComment, get_current_task_user, get_next_task_number
from services.archive_service import auto_archive_completed_tasks
from services.import_service import parse_import_text, detect_duplicates_with_ai, create_imported_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])

VALID_STATUSES = {"pending", "in_progress", "done", "archived"}
VALID_PRIORITIES = {"low", "medium", "high", "urgent"}


# --- Pydantic schemas ---

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: Optional[str] = "medium"
    due_date: Optional[date] = None
    category_id: Optional[int] = None
    assigned_to: Optional[int] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[date] = None
    category_id: Optional[int] = None
    assigned_to: Optional[int] = None


class StatusUpdate(BaseModel):
    status: str


class ImportBody(BaseModel):
    text: str
    assigned_to: Optional[int] = 1


# --- Serializers ---

def serialize_user(user):
    if not user:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "role": user.role,
    }


def serialize_category(cat):
    if not cat:
        return None
    return {
        "id": cat.id,
        "name": cat.name,
        "color": cat.color,
        "icon": cat.icon,
        "sort_order": cat.sort_order,
    }


def serialize_task(task):
    return {
        "id": task.id,
        "task_number": task.task_number,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "category_id": task.category_id,
        "created_by": task.created_by,
        "assigned_to": task.assigned_to,
        "completed_at": task.completed_at,
        "archived_at": task.archived_at,
        "created_at": task.created_at,
        "last_updated": task.last_updated,
        "creator": serialize_user(task.creator),
        "assignee": serialize_user(task.assignee),
        "category": serialize_category(task.category),
        "comment_count": len(task.comments) if task.comments else 0,
    }


def _load_task(db: Session, task_id: int):
    return db.query(Task).options(
        joinedload(Task.creator),
        joinedload(Task.assignee),
        joinedload(Task.category),
        joinedload(Task.comments),
    ).filter(Task.id == task_id).first()


# --- Endpoints ---

@router.get("/archive")
def list_archive(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    cutoff = datetime.utcnow() - timedelta(days=30)
    tasks = db.query(Task).options(
        joinedload(Task.creator),
        joinedload(Task.assignee),
        joinedload(Task.category),
        joinedload(Task.comments),
    ).filter(
        Task.status == "archived",
        Task.archived_at >= cutoff,
    ).order_by(Task.archived_at.desc()).all()

    return {"success": True, "data": [serialize_task(t) for t in tasks], "error": None, "message": None}


@router.post("/import")
def import_tasks(
    body: ImportBody,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    parsed = parse_import_text(text)
    if not parsed:
        raise HTTPException(status_code=400, detail="No tasks could be parsed from the text")

    existing_tasks = db.query(Task).filter(Task.status != "archived").all()
    existing_for_ai = [
        {"id": t.id, "task_number": t.task_number, "title": t.title}
        for t in existing_tasks
    ]

    analyzed = detect_duplicates_with_ai(parsed, existing_for_ai)
    to_create = [t for t in analyzed if not t["is_duplicate"]]
    duplicates = [t for t in analyzed if t["is_duplicate"]]

    created_tasks = create_imported_tasks(db, to_create, assigned_to=body.assigned_to, created_by=current_user.id)
    db.commit()

    created_ids = [t.id for t in created_tasks]
    if created_ids:
        created_tasks = db.query(Task).options(
            joinedload(Task.creator),
            joinedload(Task.assignee),
            joinedload(Task.category),
            joinedload(Task.comments),
        ).filter(Task.id.in_(created_ids)).all()

    return {
        "success": True,
        "data": {
            "created": [serialize_task(t) for t in created_tasks],
            "duplicates": [
                {
                    "title": d["title"],
                    "task_number": d.get("task_number"),
                    "matched_existing_id": d.get("matched_existing_id"),
                    "reason": d.get("match_reason"),
                }
                for d in duplicates
            ],
            "total_parsed": len(parsed),
            "total_created": len(created_tasks),
            "total_duplicates": len(duplicates),
        },
        "error": None,
        "message": f"{len(created_tasks)} tareas creadas, {len(duplicates)} duplicadas omitidas",
    }


@router.get("")
def list_tasks(
    status: Optional[str] = Query(None),
    assigned_to: Optional[int] = Query(None),
    created_by: Optional[int] = Query(None),
    priority: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    due_before: Optional[date] = Query(None),
    due_after: Optional[date] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0),
    limit: int = Query(50),
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    auto_archive_completed_tasks(db)

    query = db.query(Task).options(
        joinedload(Task.creator),
        joinedload(Task.assignee),
        joinedload(Task.category),
        joinedload(Task.comments),
    )

    if status:
        query = query.filter(Task.status == status)
    else:
        query = query.filter(Task.status != "archived")

    if assigned_to is not None:
        query = query.filter(Task.assigned_to == assigned_to)

    if created_by is not None:
        query = query.filter(Task.created_by == created_by)

    if priority:
        query = query.filter(Task.priority == priority)

    if category_id is not None:
        if category_id in ("0", "none"):
            query = query.filter(Task.category_id.is_(None))
        else:
            query = query.filter(Task.category_id == int(category_id))

    if due_before:
        query = query.filter(Task.due_date <= due_before)

    if due_after:
        query = query.filter(Task.due_date >= due_after)

    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(Task.title.ilike(term), Task.description.ilike(term))
        )

    tasks = query.order_by(Task.created_at.desc()).offset(skip).limit(limit).all()
    return {"success": True, "data": [serialize_task(t) for t in tasks], "error": None, "message": None}


@router.get("/{task_id}")
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task = db.query(Task).options(
        joinedload(Task.creator),
        joinedload(Task.assignee),
        joinedload(Task.category),
        joinedload(Task.comments).joinedload(TaskComment.user),
    ).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task_data = serialize_task(task)
    task_data["comments"] = [
        {
            "id": c.id,
            "task_id": c.task_id,
            "user_id": c.user_id,
            "content": c.content,
            "created_at": c.created_at,
            "last_updated": c.last_updated,
            "user": serialize_user(c.user),
        }
        for c in task.comments
    ]
    return {"success": True, "data": task_data, "error": None, "message": None}


@router.post("", status_code=201)
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Title is required")

    if body.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of: {', '.join(VALID_PRIORITIES)}")

    task = Task(
        title=body.title.strip(),
        description=body.description.strip() if body.description else None,
        priority=body.priority,
        due_date=body.due_date,
        category_id=body.category_id,
        assigned_to=body.assigned_to,
        created_by=current_user.id,
        task_number=get_next_task_number(db),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    task = _load_task(db, task.id)
    return {"success": True, "data": serialize_task(task), "error": None, "message": "Task created"}


@router.put("/{task_id}")
def update_task(
    task_id: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        task.title = body.title.strip()

    if body.description is not None:
        task.description = body.description.strip() if body.description else None

    if body.priority is not None:
        if body.priority not in VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail="Invalid priority")
        task.priority = body.priority

    if body.due_date is not None:
        task.due_date = body.due_date

    if body.category_id is not None:
        task.category_id = body.category_id

    if body.assigned_to is not None:
        task.assigned_to = body.assigned_to

    task.last_updated = datetime.utcnow()
    db.commit()

    task = _load_task(db, task_id)
    return {"success": True, "data": serialize_task(task), "error": None, "message": "Task updated"}


@router.put("/{task_id}/status")
def update_task_status(
    task_id: int,
    body: StatusUpdate,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")

    old_status = task.status
    task.status = body.status

    if body.status == "done" and old_status != "done":
        task.completed_at = datetime.utcnow()
    elif body.status != "done" and old_status == "done":
        task.completed_at = None

    if body.status == "archived":
        task.archived_at = datetime.utcnow()
        task.task_number = None
    elif old_status == "archived" and body.status != "archived":
        task.archived_at = None
        task.task_number = get_next_task_number(db)

    task.last_updated = datetime.utcnow()
    db.commit()

    task = _load_task(db, task_id)
    return {"success": True, "data": serialize_task(task), "error": None, "message": f"Status changed to {body.status}"}


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = "archived"
    task.archived_at = datetime.utcnow()
    task.task_number = None
    task.last_updated = datetime.utcnow()
    db.commit()

    return {"success": True, "data": {"id": task_id}, "error": None, "message": "Task archived"}
