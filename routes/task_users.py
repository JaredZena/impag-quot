from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import get_db, TaskUser, get_current_task_user

router = APIRouter(prefix="/task-users", tags=["task-users"])


def serialize_user(user):
    if not user:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "role": user.role,
        "is_active": user.is_active,
    }


@router.get("")
def list_users(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    users = db.query(TaskUser).filter(TaskUser.is_active == True).all()
    return {"success": True, "data": [serialize_user(u) for u in users], "error": None, "message": None}


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    user = get_current_task_user(db, token_data["email"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found in task system")
    return {"success": True, "data": serialize_user(user), "error": None, "message": None}
