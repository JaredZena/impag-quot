from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from datetime import datetime

from auth import verify_google_token
from models import get_db, Task, TaskComment, TaskUser, get_current_task_user

router = APIRouter(prefix="/tasks", tags=["task-comments"])


class CommentBody(BaseModel):
    content: str


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


def serialize_comment(comment):
    if not comment:
        return None
    return {
        "id": comment.id,
        "task_id": comment.task_id,
        "user_id": comment.user_id,
        "content": comment.content,
        "created_at": comment.created_at,
        "last_updated": comment.last_updated,
        "user": serialize_user(comment.user),
    }


@router.get("/{task_id}/comments")
def list_comments(
    task_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    comments = db.query(TaskComment).options(
        joinedload(TaskComment.user)
    ).filter(
        TaskComment.task_id == task_id
    ).order_by(TaskComment.created_at.asc()).all()

    return {"success": True, "data": [serialize_comment(c) for c in comments], "error": None, "message": None}


@router.post("/{task_id}/comments", status_code=201)
def create_comment(
    task_id: int,
    body: CommentBody,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    comment = TaskComment(
        task_id=task_id,
        user_id=current_user.id,
        content=body.content.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)

    return {"success": True, "data": serialize_comment(comment), "error": None, "message": "Comment added"}


@router.put("/{task_id}/comments/{comment_id}")
def update_comment(
    task_id: int,
    comment_id: int,
    body: CommentBody,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    comment = db.query(TaskComment).filter(
        TaskComment.id == comment_id,
        TaskComment.task_id == task_id,
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own comments")

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Content is required")

    comment.content = body.content.strip()
    comment.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(comment)

    return {"success": True, "data": serialize_comment(comment), "error": None, "message": "Comment updated"}


@router.delete("/{task_id}/comments/{comment_id}")
def delete_comment(
    task_id: int,
    comment_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    current_user = get_current_task_user(db, token_data["email"])
    if not current_user:
        raise HTTPException(status_code=404, detail="User not found in task system")

    comment = db.query(TaskComment).filter(
        TaskComment.id == comment_id,
        TaskComment.task_id == task_id,
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")

    db.delete(comment)
    db.commit()

    return {"success": True, "data": {"id": comment_id}, "error": None, "message": "Comment deleted"}
