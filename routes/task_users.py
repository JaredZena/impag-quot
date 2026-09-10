from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import verify_google_token
from models import TaskUser, get_current_task_user, get_db

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
    return {
        "success": True,
        "data": [serialize_user(u) for u in users],
        "error": None,
        "message": None,
    }


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_google_token),
):
    email = token_data["email"]
    user = get_current_task_user(db, email)
    if user is None:
        # Every allowlisted Google account belongs in the task system: create
        # the task user on first visit instead of failing the landing page
        # (/tasks) with a 404. Never resurrect someone an admin deactivated.
        if db.query(TaskUser).filter(TaskUser.email == email).first():
            raise HTTPException(
                status_code=403,
                detail="Tu usuario está desactivado. Pide acceso al administrador.",
            )
        name = (token_data.get("name") or "").strip() or email.split("@")[0]
        user = TaskUser(
            email=email,
            display_name=name[:100],
            avatar_url=(token_data.get("picture") or None),
            role="member",
            is_active=True,
        )
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # A parallel first request created it a moment ago.
            db.rollback()
            user = get_current_task_user(db, email)
            if user is None:
                raise HTTPException(
                    status_code=500, detail="No se pudo crear el usuario"
                ) from None
        else:
            db.refresh(user)
    return {
        "success": True,
        "data": serialize_user(user),
        "error": None,
        "message": None,
    }
